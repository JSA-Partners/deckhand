"""The amend step: a discovery, or the ticked findings of a review, reaches the story it belongs to.

`context` prints the body to edit and the latest review in full, because the ticks in it are the
amendment. `apply` has two modes, and they are the same decision the process has always made about a
discovery: `--note` keeps the work in this story, and `--new-issue` gives it its own story, blocked
by this one, which is what the split step used to do.

The body mode never rewrites more than the model drafted: the draft's section headings have to match
the ones the issue carries, so a body that lost a section is a refusal rather than a silent deletion.
A review's decisions reach Notes only once a human has ticked one of them, and each finding keeps one
line there however many amends a story takes.
"""

from __future__ import annotations

import argparse
import datetime
import re
from pathlib import Path

from deckhand import gh, issue, lint, sections
from deckhand.step import Refusal, block, draft_line, read_draft, reason, refuse_stub, step, usable

DRAFT_RULE = "Write the whole edited body to the draft; keep every section heading."
REVIEW_HEADING = "## Latest review"

_DECISIONS = re.compile(r"^#+\s+Decisions\s*$")
_HEADING = re.compile(r"^#+\s")
# One task list box in the review's Decisions list: the tick, then the finding's id.
_BOX = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(\S+)")


def _draft_name(number: int) -> str:
    """The draft file for issue `number`, inside the repository's cache directory."""
    return f"{number}-body.md"


def next_line(number: int) -> str:
    """The one line both modes end on: an amend never decides what happens next by itself."""
    return f"Next: continue where execution stopped, or reply Approved and /deckhand:ready {number}."


# --- context ----------------------------------------------------------------


def _story(number: int) -> issue.Issue | Exception:
    """The issue, or the failure to read it; the body and the review share the one lookup."""
    try:
        return issue.view(gh.repo_slug(), number)
    except Exception as error:
        return error


def _body_block(story: issue.Issue | Exception) -> str:
    """The body to edit, or one line saying why it could not be read."""
    if isinstance(story, Exception):
        return f"Body: unavailable ({reason(story)})"
    return story.body.strip("\n")


def _review_lines(story: issue.Issue | Exception) -> list[str]:
    """The latest review comment in full, or `  none`; the ticks in it are the amendment."""
    review = issue.review_comment(usable(story))
    if review is None:
        return ["  none"]
    return review.body.strip("\n").splitlines()


def context(args: argparse.Namespace) -> int:
    """Print the body to edit, the latest review in full, and where the edited body goes."""
    story = _story(args.issue)
    print(_body_block(story))
    print()
    block(REVIEW_HEADING, lambda: _review_lines(story))
    print()
    print(draft_line("Draft", _draft_name(args.issue)))
    print(DRAFT_RULE)
    return 0


# --- apply ------------------------------------------------------------------


def decisions(review: str) -> list[tuple[str, bool]]:
    """`(id, accepted)` for each box in the review's Decisions list, in the order they appear."""
    found: list[tuple[str, bool]] = []
    inside = False
    for line in review.splitlines():
        if _DECISIONS.match(line):
            inside = True
            continue
        if inside and _HEADING.match(line):
            break
        box = _BOX.match(line) if inside else None
        if box:
            found.append((box.group(2), box.group(1).lower() == "x"))
    return found


def triaged(story: issue.Issue) -> list[tuple[str, bool]]:
    """The latest review's decisions, but only once a human has ticked one of them.

    An amend can run at any point in a story's life, and most of them have nothing to do with a
    review. Recording every box the moment a review is posted would write the whole list off as
    rejected before anyone had read it, so an untriaged list records nothing at all.
    """
    review = issue.review_comment(story)
    found = decisions(review.body if review else "")
    return found if any(accepted for _, accepted in found) else []


def _verdict(line: str, name: str, verdict: str) -> str | None:
    """`line` with its verdict set to `verdict` when it is `name`'s line, else None.

    Only the verdict word changes, so a line a human added words to keeps them when a later review
    flips it.
    """
    match = re.match(rf"^(-\s+)(?:Accepted|Rejected)(\s+{re.escape(name)}(?:\s.*)?)$", line)
    return None if match is None else f"{match.group(1)}{verdict}{match.group(2)}"


def _amended(notes: str, note: str, story: issue.Issue) -> str:
    """`notes` with the amend line, then this review's decisions, each recorded on one line only."""
    lines = notes.split("\n") if notes else []
    lines.append(f"- Amended {datetime.datetime.now(datetime.UTC).date().isoformat()}: {note}")
    for name, accepted in triaged(story):
        verdict = "Accepted" if accepted else "Rejected"
        for index, line in enumerate(lines):
            rewritten = _verdict(line, name, verdict)
            if rewritten is not None:  # an earlier amend recorded it; a flipped verdict wins
                lines[index] = rewritten
                break
        else:
            lines.append(f"- {verdict} {name}")
    return "\n".join(lines)


def _same_headings(draft: str, body: str) -> None:
    """Refuse a draft whose sections are not the issue's own, in the issue's own order."""
    drafted = [name for name, _ in sections.parse(draft)[1]]
    current = [name for name, _ in sections.parse(body)[1]]
    if drafted != current:
        raise Refusal(f"headings changed: expected {', '.join(current) or 'none'}; got {', '.join(drafted) or 'none'}")


def _amend(repo: str, number: int, draft: str, note: str, story: issue.Issue) -> int:
    """Put the drafted body on the issue, with the amend line and the review's decisions in Notes."""
    note = " ".join(note.split())
    if not note:
        raise Refusal("--note needs a line saying what changed and why")
    _same_headings(draft, story.body)
    notes = _amended(sections.get(draft, "Notes", ""), note, story)
    body = lint.checked(sections.replace(draft, "Notes", notes))
    issue.update_body(repo, number, body)
    print(f"Updated #{number} {story.url}")
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
    print(next_line(number))
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
