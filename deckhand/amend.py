"""The amend step: the decisions of a review, or a discovery, reach the story they belong to.

`context` prints the board's column, because it says which moment of the story this is and whether
the body can still change, where the body to edit was written, and the latest `Review:` entry in
full, because the decisions recorded in it are what the amend applies; the decisions were made in
the session, and nothing a person wrote on GitHub is read. `apply` has two modes, and they are the
same decision the process has always made about a discovery: `--note` keeps the work in this story,
and `--new-issue` gives it its own story, blocked by this one, which is what the split step used to
do.

The body mode never rewrites more than the model drafted: the draft's section headings have to match
the ones the issue carries, so a body that lost a section is a refusal rather than a silent deletion.
The body freezes once the story starts, which the board says as In Progress, Pending Review, or
Done: a discovery during execution is a `Deviation:` entry or a new issue, never a rewritten story.

The record is the issue's own log, not a block inside the story: every amend posts one `Amended:`
entry, so the body the draft carries reaches GitHub as it was drafted and the log reads in the order
it was written. A split boards the new story as Draft and logs `Drafted:` there and `Split:` here.

A stub takes `--note` too: its Requirements change and its Stories list comes back as it was, since
stories change only through a split. `--repo` reaches an issue in another repository, for a build
whose decision changed an issue there that has not started.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from deckhand import config, fields, gh, invoke, issue, lint, log, sections, stub
from deckhand.lint import RULES as BODY_RULES
from deckhand.new import skeleton
from deckhand.park import board_draft
from deckhand.step import (
    Refusal,
    block,
    draft_line,
    fits_title,
    indented,
    read_draft,
    reason,
    resolved_settings,
    spill,
    step,
    usable,
)

DRAFT_RULE = "Write the whole edited body to the draft; keep every section heading."
REVIEW_HEADING = "## Latest review"
SPLIT_DRAFTED = "Drafted: the story and its plan, split from this one."

FROZEN = "the story is frozen once it starts; log a Deviation, or open a new issue with --new-issue"
FROZEN_CONTEXT = (
    "The body is frozen once the story starts.\n"
    "  A discovery goes in a Deviation: entry.\n"
    "  Work of its own goes in a new issue.\n"
    "  Only the title can still change."
)
STARTED = ("In Progress", "Pending Review", "Done")
STUB_RULE = "Write the whole edited stub to the draft; change the Requirements and keep the Stories list as it is."
STORIES_CHANGED = "the Stories list changed; a stub's stories change only through a split"
ELSEWHERE = "--new-issue opens its story in this repository; --repo is only for --note"


def _draft_name(number: int) -> str:
    """The draft file for issue `number`, inside the repository's cache directory."""
    return f"{number}-body.md"


def _status(repo: str, number: int) -> str | None:
    """The board's Status for the story, or None off the board and when the board cannot be read.

    It is read once, before the write, because the freeze turns on it. A board that cannot be read
    at all leaves the body free to change, since a story is stopped by a rule it broke, never by a
    lookup that failed.
    """
    try:
        return fields.get_field(config.load(), repo, number, "Status")
    except Exception:
        return None


def _repo(flag: str | None) -> str:
    """The repository the issue is in: the one `--repo` names, else this one."""
    if flag is None:
        return gh.repo_slug()
    try:
        gh.split_repo(flag)
    except gh.GhError as error:
        raise Refusal(str(error)) from error
    return flag


# --- context ----------------------------------------------------------------


def _story(repo: str | Exception, number: int) -> issue.Issue | Exception:
    """The issue, or the failure to read it; the body and the review share the one lookup."""
    try:
        return issue.view(usable(repo), number)
    except Exception as error:
        return error


def _status_line(repo: str | Exception, number: int) -> str:
    """The board's column, which is the moment the story is at and whether its body is frozen.

    One read, and never a failure: a context that cannot reach the board still has a body to edit,
    and the freeze is enforced by `apply`, which reads the board again before it writes.
    """
    try:
        status = fields.get_field(config.load(), usable(repo), number, "Status")
    except Exception as error:
        return f"Status: unavailable ({reason(error)})"
    return f"Status: {status or 'off the board'}"


def _review_lines(story: issue.Issue | Exception) -> list[str]:
    """The latest `Review:` entry in full, or `  none`; its decisions are what the amend applies."""
    entry = log.last(usable(story), "Review:")
    return entry.body.strip("\n").splitlines() if entry else ["  none"]


def context(args: argparse.Namespace) -> int:
    """Print the title, the column, where the body was written, the latest review, and where the draft goes."""
    try:
        repo: str | Exception = _repo(args.repo)
    except Exception as error:
        repo = error
    story = _story(repo, args.issue)
    a_stub = not isinstance(story, Exception) and stub.is_stub(story.body)
    status = _status_line(repo, args.issue)
    print(f"Title: {story.title}" if not isinstance(story, Exception) else f"Title: unavailable ({reason(story)})")
    print(status)
    print()
    print(spill("Body", f"{args.issue}-issue.md", lambda: sections.bare(usable(story).body), args.repo))
    print()
    if status.removeprefix("Status: ") in STARTED:
        print(FROZEN_CONTEXT)
        print()
        block(REVIEW_HEADING, lambda: _review_lines(story))
        print()
        print(invoke.apply_line("amend", str(args.issue), '--title "<title>"', '--note "<why>"'))
        return 0
    if not a_stub:  # a stub's shape is the body above, and its stories are not the draft's to change
        block("Shape:", lambda: indented(skeleton().splitlines()))
        print()
    block(REVIEW_HEADING, lambda: _review_lines(story))
    print()
    print(draft_line("Draft", _draft_name(args.issue), args.repo))
    print(STUB_RULE if a_stub else DRAFT_RULE)
    print(invoke.apply_line("amend", str(args.issue), "<draft>", '--note "<why>"'))
    return 0


# --- apply ------------------------------------------------------------------


def _same_headings(draft: str, body: str) -> None:
    """Refuse a draft whose sections are not the issue's own, in the issue's own order."""
    drafted = [name for name, _ in sections.parse(draft)[1]]
    current = [name for name, _ in sections.parse(body)[1]]
    if drafted != current:
        raise Refusal(f"headings changed: expected {', '.join(current) or 'none'}; got {', '.join(drafted) or 'none'}")


def _title(flag: str | None, story: issue.Issue) -> str | None:
    """The new title, checked against the subject limit, or None when the title stands."""
    title = " ".join((flag or "").split())
    if not title or title == story.title:
        return None
    return fits_title(title)


def _note(note: str) -> str:
    """The note as one line; a blank one says nothing about what changed, so it is refused."""
    note = " ".join(note.split())
    if not note:
        raise Refusal("--note needs a line saying what changed and why")
    return note


def _write(repo: str, number: int, body: str, title: str | None, note: str, story: issue.Issue) -> None:
    """Put the body and title on the issue and log the amend, printing each write as it lands."""
    issue.update_body(repo, number, body)
    # Flushed as it is printed: the body is already on GitHub, and a comment that fails below has to
    # leave the edit where the user can see it rather than in a buffer that never reaches the screen.
    print(f"Updated #{number} {story.url}", flush=True)
    if title is not None:
        issue.set_title(repo, number, title)
        print(f"Title: {title}", flush=True)
    issue.comment(repo, number, log.checked(f"Amended: {note}"))
    print("Logged Amended")


def _retitle(repo: str, number: int, note: str, title_flag: str | None, story: issue.Issue) -> int:
    """Change the title alone, at any Status: the freeze guards the body, and a title is not the body.

    `finish` builds the pull request subject from this title, so a wrong one has to be fixable while
    the story runs; the subject check is the same one every title deckhand writes goes through.
    """
    note = _note(note)
    title = _title(title_flag, story)
    if title is None:
        raise Refusal("--title needs a title that differs from the story's")
    issue.set_title(repo, number, title)
    print(f"Title: {title}", flush=True)
    issue.comment(repo, number, log.checked(f"Amended: {note}"))
    print("Logged Amended")
    return 0


def _amend(repo: str, number: int, draft: str, note: str, title_flag: str | None, story: issue.Issue) -> int:
    """Put the drafted body and title on the issue and log the amend, unless the story is frozen.

    The body is the draft as it was written: what changed and why goes on the issue's own log, where
    a later step reads it, and never into the story the draft is a rewrite of. The title is checked
    here against the subject the pull request will carry, because a title is written far more often
    than a pull request is opened and the refusal belongs at the write.
    """
    note = _note(note)
    title = _title(title_flag, story)
    _same_headings(draft, story.body)
    body = lint.checked(draft)
    if _status(repo, number) in STARTED:
        raise Refusal(FROZEN)
    _write(repo, number, body, title, note, story)
    if (room := lint.headroom(body)) is not None:
        print(room)
    return 0


def _amend_stub(repo: str, number: int, draft: str, note: str, title_flag: str | None, story: issue.Issue) -> int:
    """Rewrite a stub's requirements; its stories come back as they were, because a split owns them."""
    note = _note(note)
    title = _title(title_flag, story)
    if not stub.is_stub(draft):
        raise Refusal(f"a stub starts with {stub.STUB_HEADING}")
    requirements, entries = stub.read(draft)
    if not requirements.strip():
        raise Refusal(f"{stub.STUB_HEADING} has no text")
    if entries != stub.read(story.body)[1]:
        raise Refusal(STORIES_CHANGED)
    _write(repo, number, stub.render(requirements, entries), title, note, story)
    return 0


def _new_issue(repo: str, number: int, draft: str, title: str, before: bool) -> int:
    """Open the drafted body as its own story, record which way the dependency runs, and log both ends.

    `before` is work that has to land first, so this story waits on the new one; without it the new
    story is follow-on work and waits on this one, which is the common case and the default. The
    `Split:` entry names the direction, so the log and GitHub can never disagree about it.
    """
    title = " ".join(title.split())
    if not title:
        raise Refusal("--new-issue needs a title")
    fits_title(title)
    body = lint.checked(draft)
    settings = resolved_settings()
    new, url = issue.create(repo, title, body)
    # Printed before the links are written: the issue exists from here on, and a failure below has
    # to leave the number where the user can see it rather than in a lost temp file.
    print(f"Created #{new} {url}", flush=True)
    if before:
        issue.add_dependency(repo, number, blocked_by=(repo, new))
        print(f"Blocks #{number}", flush=True)
    else:
        issue.add_dependency(repo, new, blocked_by=(repo, number))
        print(f"Blocked by #{number}", flush=True)
    board_draft(settings, repo, new, url, SPLIT_DRAFTED)
    ran = "which this story waits on" if before else "blocked by this story"
    issue.comment(repo, number, log.checked(f"Split: #{new} {title}, {ran}."))
    print("Logged Split")
    return 0


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path, nargs="?", help="the drafted body; omit it to change the title alone")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--note", help="one line saying what changed and why")
    mode.add_argument("--new-issue", metavar="TITLE", help="open the draft as its own blocked story")
    parser.add_argument("--title", help="with --note: the story's new title, alone when no draft is given")
    parser.add_argument("--repo", metavar="OWNER/NAME", help="with --note: the issue's repository, when not this one")
    parser.add_argument(
        "--before",
        action="store_true",
        help="with --new-issue: the new story lands first, so this story waits on it",
    )
    # Only the parser that read these arguments knows the usage line to print a usage error with.
    parser.set_defaults(usage=parser)


def _configure_context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", metavar="OWNER/NAME", help="the issue's repository, when not this one")


@step("amend", _configure, rules=BODY_RULES, configure_context=_configure_context)
def apply(args: argparse.Namespace) -> int:
    """Amend a story or a stub from the drafted body, or split the draft out as its own blocked story."""
    if args.before and args.new_issue is None:
        args.usage.error("--before is only for --new-issue")
    if args.repo is not None and args.new_issue is not None:
        raise Refusal(ELSEWHERE)
    repo = _repo(args.repo)
    story = issue.view(repo, args.issue)
    if args.file is None:
        if args.new_issue is not None:
            args.usage.error("--new-issue needs the draft to open as the new story")
        if args.title is None:
            args.usage.error("give a draft file, or --title to change the title alone")
        return _retitle(repo, args.issue, args.note, args.title, story)
    draft = read_draft(args.file)
    if stub.is_stub(story.body):
        if args.new_issue is not None:
            raise Refusal(f"#{args.issue} is a stub; amend it with --note")
        return _amend_stub(repo, args.issue, draft, args.note, args.title, story)
    if args.new_issue is not None:
        if args.title is not None:
            raise Refusal("--title goes with --note; --new-issue carries its title as its argument")
        return _new_issue(repo, args.issue, draft, args.new_issue, args.before)
    return _amend(repo, args.issue, draft, args.note, args.title, story)
