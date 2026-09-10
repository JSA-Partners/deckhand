"""The amend step: the decisions of a review, or a discovery, reach the story they belong to.

`context` prints the board's column, because it says which moment of the story this is and whether
the body can still change, the body to edit, and the latest `Review:` entry in full, because the
decisions recorded in it are what the amend applies; the decisions were made in the session, and
nothing a person wrote on GitHub is read. `apply` has two modes, and they are the same decision the
process has always made about a discovery: `--note` keeps the work in this story, and `--new-issue`
gives it its own story, blocked by this one, which is what the split step used to do.

The body mode never rewrites more than the model drafted: the draft's section headings have to match
the ones the issue carries, so a body that lost a section is a refusal rather than a silent deletion.
The body freezes once the story starts, which the board says as In Progress, Pending Review, or
Done: a discovery during execution is a `Deviation:` entry or a new issue, never a rewritten story.

The record is the issue's own log, not a block inside the story: every amend posts one `Amended:`
entry, so the body the draft carries reaches GitHub as it was drafted and the log reads in the order
it was written. A split boards the new story as Draft and logs `Drafted:` there and `Split:` here.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from deckhand import config, fields, gh, issue, lint, log, sections
from deckhand.new import board_draft
from deckhand.step import (
    Refusal,
    block,
    draft_line,
    fits_title,
    read_draft,
    reason,
    refuse_stub,
    resolved_settings,
    step,
    usable,
)

DRAFT_RULE = "Write the whole edited body to the draft; keep every section heading."
BODY_HEADING = "## Body"
REVIEW_HEADING = "## Latest review"
SPLIT_DRAFTED = "Drafted: the story and its plan, split from this one."

FROZEN = "the story is frozen once it starts; log a Deviation, or open a new issue with --new-issue"
STARTED = ("In Progress", "Pending Review", "Done")


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


# --- context ----------------------------------------------------------------


def _story(number: int) -> issue.Issue | Exception:
    """The issue, or the failure to read it; the body and the review share the one lookup."""
    try:
        return issue.view(gh.repo_slug(), number)
    except Exception as error:
        return error


def _status_line(number: int) -> str:
    """The board's column, which is the moment the story is at and whether its body is frozen.

    One read, and never a failure: a context that cannot reach the board still has a body to edit,
    and the freeze is enforced by `apply`, which reads the board again before it writes.
    """
    try:
        status = fields.get_field(config.load(), gh.repo_slug(), number, "Status")
    except Exception as error:
        return f"Status: unavailable ({reason(error)})"
    return f"Status: {status or 'off the board'}"


def _body_lines(story: issue.Issue | Exception) -> list[str]:
    """The body to edit, under its own heading, so nothing above it reads as part of the story.

    The plan comes out of its fold here: the fold is how the body is written to GitHub, and a draft
    that copied it back would be editing the wrapper as if it were the story's own text.
    """
    return sections.bare(usable(story).body).strip("\n").splitlines()


def _review_lines(story: issue.Issue | Exception) -> list[str]:
    """The latest `Review:` entry in full, or `  none`; its decisions are what the amend applies."""
    entry = log.last(usable(story), "Review:")
    return entry.body.strip("\n").splitlines() if entry else ["  none"]


def context(args: argparse.Namespace) -> int:
    """Print the title, the column, the body to edit, the latest review, and where the draft goes."""
    story = _story(args.issue)
    print(f"Title: {story.title}" if not isinstance(story, Exception) else f"Title: unavailable ({reason(story)})")
    print(_status_line(args.issue))
    print()
    block(BODY_HEADING, lambda: _body_lines(story))
    print()
    block(REVIEW_HEADING, lambda: _review_lines(story))
    print()
    print(draft_line("Draft", _draft_name(args.issue)))
    print(DRAFT_RULE)
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


def _amend(repo: str, number: int, draft: str, note: str, title_flag: str | None, story: issue.Issue) -> int:
    """Put the drafted body and title on the issue and log the amend, unless the story is frozen.

    The body is the draft as it was written: what changed and why goes on the issue's own log, where
    a later step reads it, and never into the story the draft is a rewrite of. The title is checked
    here against the subject the pull request will carry, because a title is written far more often
    than a pull request is opened and the refusal belongs at the write.
    """
    note = " ".join(note.split())
    if not note:
        raise Refusal("--note needs a line saying what changed and why")
    title = _title(title_flag, story)
    _same_headings(draft, story.body)
    body = lint.checked(draft)
    if _status(repo, number) in STARTED:
        raise Refusal(FROZEN)
    issue.update_body(repo, number, body)
    # Flushed as it is printed: the body is already on GitHub, and a comment that fails below has to
    # leave the edit where the user can see it rather than in a buffer that never reaches the screen.
    print(f"Updated #{number} {story.url}", flush=True)
    if title is not None:
        issue.set_title(repo, number, title)
        print(f"Title: {title}", flush=True)
    issue.comment(repo, number, log.checked(f"Amended: {note}"))
    print("Logged Amended")
    return 0


def _new_issue(repo: str, number: int, draft: str, title: str) -> int:
    """Open the drafted body as its own story, blocked by this one, board it as Draft, and log both ends."""
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
    issue.add_dependency(repo, new, blocked_by=number)
    print(f"Blocked by #{number}", flush=True)
    board_draft(settings, repo, new, url, SPLIT_DRAFTED)
    issue.comment(repo, number, log.checked(f"Split: #{new} {title}, blocked by this story."))
    print("Logged Split")
    return 0


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path, help="the drafted body")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--note", help="one line saying what changed and why")
    mode.add_argument("--new-issue", metavar="TITLE", help="open the draft as its own blocked story")
    parser.add_argument("--title", help="with --note: the story's new title")


@step("amend", _configure)
def apply(args: argparse.Namespace) -> int:
    """Amend a story from the drafted body, or split the draft out as its own blocked story."""
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    refuse_stub(args.issue, story.body)
    draft = read_draft(args.file)
    if args.new_issue is not None:
        if args.title is not None:
            raise Refusal("--title goes with --note; --new-issue carries its title as its argument")
        return _new_issue(repo, args.issue, draft, args.new_issue)
    return _amend(repo, args.issue, draft, args.note, args.title, story)
