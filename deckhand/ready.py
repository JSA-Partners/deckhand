"""The ready step: a reviewed story moves to Backlog with its kind, its points, and its blockers.

`context` prints the kinds the project defines, the story itself, the open blockers, the fields as
the board has them, and the analogy table the estimate comes from. The kinds and the story are what
the board question is answered from, and the question is asked in the turn this prints into. Each
block degrades to one line of its own, so a lookup that fails never costs the model the rest of the
prompt.

`apply` validates the kind, the points, the review, and every blocker before it writes anything,
then records the dependencies and sets the fields, printing one line per write. A story is already
on the board as Draft from the moment it was written, so the item is added only for a story from
before that, one not on the board yet. The latest `Review:` entry is the whole gate: nobody has to
reply, and Status is always Backlog, because the board has no Blocked column and `start` reads the
blockers live. The board's own automation sets Status after an item is added, asynchronously, so
when `apply` has added one it waits, reads Status back, and puts it right once if the automation
moved it. `DECKHAND_SETTLE` is that wait in seconds; the tests set it to 0.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from typing import Any

from deckhand import board, config, fields, gh, issue, log, sections
from deckhand.config import Settings
from deckhand.fields import format_number
from deckhand.step import (
    Refusal,
    block,
    blockers_block,
    issue_number,
    reason,
    refuse_stub,
    settings_or_error,
    step,
    usable,
)

FIELDS = ("Kind", "Story Points", "Actual")
LIMIT = 20
SETTLE = 2.0
TABLE_HEADER = "| # | Repo | Title | Estimate | Actual | Tasks | Note |"
TABLE_RULE = "| --- | --- | --- | --- | --- | --- | --- |"

_TASK_HEADING = re.compile(r"^### Task [0-9]+", re.MULTILINE)
# What gh says when the issue is not there, as opposed to a network, auth, or rate limit failure.
_NOT_FOUND = re.compile(r"not found|could not (find|resolve)|no issue", re.IGNORECASE)


def settle_seconds() -> float:
    """How long to wait before reading Status back; `DECKHAND_SETTLE` overrides the default.

    Never negative, and never a value `time.sleep` would reject: anything that is not a positive
    number is no wait at all.
    """
    raw = os.environ.get("DECKHAND_SETTLE")
    try:
        seconds = float(raw) if raw else SETTLE
    except ValueError:
        seconds = SETTLE
    return seconds if seconds > 0 else 0.0


# --- the analogy table ------------------------------------------------------


def _fmt(value: Any) -> str:
    return "-" if value is None else format_number(value)


def analogy_rows(nodes: list[dict[str, Any]], limit: int) -> list[str]:
    """Table rows for the most recently closed Done stories among the board's item `nodes`."""
    done = []
    for node in nodes:
        content = node.get("content")
        if not content or board.field_value(node, "Status", "name") != "Done":
            continue
        points = board.field_value(node, "Story Points", "number")
        actual = board.field_value(node, "Actual", "number")
        done.append((content, points, actual))
    done.sort(key=lambda item: item[0].get("closedAt") or "", reverse=True)
    rows = []
    for content, points, actual in done[:limit]:
        title = (content.get("title") or "").replace("|", "\\|")
        repo = ((content.get("repository") or {}).get("nameWithOwner") or "/").partition("/")[2]
        tasks = len(_TASK_HEADING.findall(content.get("body") or ""))
        note = "uncalibrated" if actual is None else ""
        rows.append(
            f"| {content.get('number')} | {repo} | {title} | {_fmt(points)} | {_fmt(actual)} | {tasks} | {note} |"
        )
    return rows


# --- context ----------------------------------------------------------------


def _kinds_line(settings: Settings | Exception) -> str:
    """The kinds this project defines, which is the list `apply` refuses anything outside of."""
    try:
        return "Kinds: " + ", ".join(usable(settings).kinds)
    except Exception as error:
        return f"Kinds: unavailable ({reason(error)})"


def _story_lines(number: int) -> list[str]:
    """The Story section, which is what the points are estimated against."""
    return sections.get(issue.view(gh.repo_slug(), number).body, "Story", "").strip("\n").splitlines()


def _fields_block(settings: Settings | Exception, number: int) -> list[str]:
    values = fields.get_fields(usable(settings), gh.repo_slug(), number, FIELDS)
    return [f"  {name}: {'unset' if value is None else value}" for name, value in values.items()]


def _table_block(settings: Settings | Exception) -> list[str]:
    rows = analogy_rows(board.items(usable(settings)), LIMIT)
    return [TABLE_HEADER, TABLE_RULE, *rows] if rows else ["  none"]


def context(args: argparse.Namespace) -> int:
    """Print the kinds, the story, the open blockers, the fields, and the Done stories to compare to."""
    settings = settings_or_error()
    print(_kinds_line(settings))
    print()
    block("## Story", lambda: _story_lines(args.issue))
    block("Blockers:", lambda: blockers_block(gh.repo_slug(), args.issue))
    block("Fields:", lambda: _fields_block(settings, args.issue))
    block(f"Done stories (last {LIMIT}):", lambda: _table_block(settings))
    return 0


# --- apply ------------------------------------------------------------------


def _points(value: str) -> int:
    if not (value.isascii() and value.isdigit()):
        raise Refusal(f"points must be a non-negative integer, got {value!r}")
    return int(value)


def _reviewed(story: issue.Issue, number: int) -> None:
    """Refuse unless the story carries a `Review:` entry, which is the only gate this step holds."""
    if log.last(story, "Review:") is None:
        raise Refusal(f"no review on #{number}; run /deckhand:next {number}")


def _open_issue(repo: str, number: int) -> None:
    """Refuse unless issue `number` exists and is open; a blocker that is neither blocks nothing.

    Only the sibling read: a blocker's comments are none of this step's business, and its state is
    the half of that read this step acts on.
    """
    try:
        state = issue.sibling(repo, number)[0]
    except gh.GhError as error:
        said = reason(error)
        if _NOT_FOUND.search(said):
            raise Refusal(f"#{number} does not exist") from error
        raise Refusal(f"#{number} cannot be read: {said}") from error
    if state.upper() != "OPEN":
        raise Refusal(f"#{number} is closed")


def _hold_status(settings: Settings, repo: str, number: int, status: str) -> None:
    """Read Status back after the board's automation has had its moment, and put it right once.

    Every write has landed by now, so a failed read is a line to report, not a run to fail.
    """
    time.sleep(settle_seconds())
    try:
        current = fields.get_field(settings, repo, number, "Status")
    except gh.GhError as error:
        print(f"Status not verified ({reason(error)})")
        return
    if current == status:
        return
    fields.set_field(settings, repo, number, "Status", status)
    print(f"Status re-set to {status} (the board's own automation had changed it)")


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kind", required=True, help="the kind of change, one the project defines")
    parser.add_argument("--points", required=True, help="the story points, a non-negative integer")
    parser.add_argument(
        "--blocked-by",
        type=issue_number,
        action="append",
        default=[],
        metavar="N",
        help="an open issue this story waits on; repeat it for more than one",
    )


@step("ready", _configure)
def apply(args: argparse.Namespace) -> int:
    """Move a reviewed story to Backlog with its kind, its points, and its blockers."""
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    refuse_stub(args.issue, story.body)
    settings = config.load()
    if args.kind not in settings.kinds:
        raise Refusal(f"kind must be one of: {', '.join(settings.kinds)}")
    points = _points(args.points)
    _reviewed(story, args.issue)
    blockers = list(dict.fromkeys(args.blocked_by or []))
    if args.issue in blockers:
        raise Refusal(f"#{args.issue} cannot block itself")
    for number in blockers:
        _open_issue(repo, number)
    for number in blockers:
        issue.add_dependency(repo, args.issue, number)
        print(f"Blocked by #{number}")
    # Every story boards in the same column: a blocker is a dependency GitHub holds and `start`
    # reads live, not a column that would need clearing when the last blocker closed.
    status = "Backlog"
    added = gh.item_id(settings, repo, args.issue) is None  # a story from before every story was boarded at birth
    if added:
        board.add(settings, story.url)
        print("Added to the board")
    print(fields.set_field(settings, repo, args.issue, "Kind", args.kind))
    print(fields.set_field(settings, repo, args.issue, "Story Points", str(points)))
    print(fields.set_field(settings, repo, args.issue, "Status", status))
    if added:
        _hold_status(settings, repo, args.issue, status)
    return 0
