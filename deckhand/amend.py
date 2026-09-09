"""The amend step: a discovery, or the ticked findings of a review, reaches the story it belongs to.

`context` prints the board's column, because it says which moment of the story this is and whether
the plan is frozen, the body to edit, the latest review in full, because the ticks in it are the
amendment, and every comment a person has left since the last amend. `apply` has two modes, and they
are the same decision the process has always made about a discovery: `--note` keeps the work in this
story, and `--new-issue` gives it its own story, blocked by this one, which is what the split step
used to do.

The body mode never rewrites more than the model drafted: the draft's section headings have to match
the ones the issue carries, so a body that lost a section is a refusal rather than a silent deletion.
The plan itself is frozen once the board says the story is In Progress: a discovery during execution
is a Notes line or a new issue, never a rewritten plan.

The record is the issue's own comments, not a block inside the story: every amend posts one
`Amended: <note>` comment, so the body the draft carries reaches GitHub as it was drafted and the
log reads in the order it was written. Both modes end on the one command a person types, so nothing
here has to say which moment of the story this was.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from deckhand import config, fields, gh, issue, lint, sections
from deckhand.step import Refusal, block, draft_line, read_draft, reason, refuse_stub, step, usable

DRAFT_RULE = "Write the whole edited body to the draft; keep every section heading."
BODY_HEADING = "## Body"
REVIEW_HEADING = "## Latest review"
FEEDBACK_HEADING = "## Feedback"

FROZEN = (
    "the plan is frozen once the story is In Progress; "
    "record the change under Notes or open a new issue with --new-issue"
)


def _draft_name(number: int) -> str:
    """The draft file for issue `number`, inside the repository's cache directory."""
    return f"{number}-body.md"


def next_line(number: int) -> str:
    """The line the note mode ends on: read the story, and when it reads right run the one command.

    The board's Status decides what happens next, and the command reads it: a story that is running
    is told to carry on, a story that is not is boarded. Neither is this line's to guess.
    """
    return f"Next: /deckhand:next {number} when it reads right."


def split_next_line(number: int, new: int) -> str:
    """The line the split mode ends on; the story being amended is already on the board and running."""
    return f"Next: carry on; /deckhand:next {new} after #{number} merges."


def _status(repo: str, number: int) -> str | None:
    """The board's Status for the story, or None off the board and when the board cannot be read.

    It is read once, before the write, because the freeze turns on it; the line printed after the
    write reads the same answer. A board that cannot be read at all leaves the plan free to change,
    since a story is stopped by a rule it broke, never by a lookup that failed.
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
    """The board's column, which is the moment the story is at and whether its plan is frozen.

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
    """The latest review comment in full, or `  none`; the ticks in it are the amendment."""
    review = issue.review_comment(usable(story))
    if review is None:
        return ["  none"]
    return review.body.strip("\n").splitlines()


def _feedback_lines(number: int) -> list[str]:
    """Every comment left since the last amend, as `- <author>: <body>`, or `  none`.

    A body of several lines keeps its shape under the line that names its author, so a reply reads
    as the person wrote it rather than as one run-on line.
    """
    lines: list[str] = []
    for reply in issue.feedback_since(gh.repo_slug(), number):
        body = reply.body.strip("\n").splitlines() or [""]
        lines.append(f"- {reply.author}: {body[0]}")
        lines.extend(f"  {rest}" for rest in body[1:])
    return lines or ["  none"]


def context(args: argparse.Namespace) -> int:
    """Print the column, the body to edit, the latest review, the feedback since, and where it goes."""
    story = _story(args.issue)
    print(_status_line(args.issue))
    print()
    block(BODY_HEADING, lambda: _body_lines(story))
    print()
    block(REVIEW_HEADING, lambda: _review_lines(story))
    print()
    block(FEEDBACK_HEADING, lambda: _feedback_lines(args.issue))
    print()
    print(draft_line("Draft", _draft_name(args.issue)))
    print(DRAFT_RULE)
    return 0


# --- apply ------------------------------------------------------------------


def _plan_lines(body: str) -> list[str]:
    """The plan's lines with their trailing whitespace off; a space at a line's end is not a change."""
    return [line.rstrip() for line in sections.get(body, "Plan", "").splitlines()]


def _same_headings(draft: str, body: str) -> None:
    """Refuse a draft whose sections are not the issue's own, in the issue's own order."""
    drafted = [name for name, _ in sections.parse(draft)[1]]
    current = [name for name, _ in sections.parse(body)[1]]
    if drafted != current:
        raise Refusal(f"headings changed: expected {', '.join(current) or 'none'}; got {', '.join(drafted) or 'none'}")


def _amend(repo: str, number: int, draft: str, note: str, story: issue.Issue) -> int:
    """Put the drafted body on the issue and post the amend as a comment, unless the plan is frozen.

    The body is the draft as it was written: what changed and why goes on the issue's own log, where
    a later amend reads it, and never into the story the draft is a rewrite of.
    """
    note = " ".join(note.split())
    if not note:
        raise Refusal("--note needs a line saying what changed and why")
    _same_headings(draft, story.body)
    status = _status(repo, number)
    if status == "In Progress" and _plan_lines(draft) != _plan_lines(story.body):
        raise Refusal(FROZEN)
    issue.update_body(repo, number, lint.checked(draft))
    # Flushed as it is printed: the body is already on GitHub, and a comment that fails below has to
    # leave the edit where the user can see it rather than in a buffer that never reaches the screen.
    print(f"Updated #{number} {story.url}", flush=True)
    issue.comment(repo, number, f"Amended: {note}")
    print(f"Commented on #{number}")
    print(next_line(number))
    return 0


def _new_issue(repo: str, number: int, draft: str, title: str) -> int:
    """Open the drafted body as its own story, blocked by this one, and say so on this one."""
    title = " ".join(title.split())
    if not title:
        raise Refusal("--new-issue needs a title")
    new, url = issue.create(repo, title, lint.checked(draft))
    # Printed before the links are written: the issue exists from here on, and a failure below has
    # to leave the number where the user can see it rather than in a lost temp file.
    print(f"Created #{new} {url}", flush=True)
    issue.add_dependency(repo, new, blocked_by=number)
    issue.comment(repo, number, f"Split: #{new} {title}, blocked by this story.")
    print(f"Blocked by #{number}")
    print(f"Commented on #{number}")
    print(split_next_line(number, new))
    return 0


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path, help="the drafted body")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--note", help="one line saying what changed and why")
    mode.add_argument("--new-issue", metavar="TITLE", help="open the draft as its own blocked story")


@step("amend", _configure)
def apply(args: argparse.Namespace) -> int:
    """Amend a story from the drafted body, or split the draft out as its own blocked story."""
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    refuse_stub(args.issue, story.body)
    draft = read_draft(args.file)
    if args.new_issue is not None:
        return _new_issue(repo, args.issue, draft, args.new_issue)
    return _amend(repo, args.issue, draft, args.note, story)
