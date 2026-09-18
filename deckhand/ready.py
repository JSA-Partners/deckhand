"""The ready step: a reviewed story moves to Backlog with its kind, its points, and its blockers.

`context` prints the rules `apply` holds, the story and its plan, the Review: entry that is the
gate, the open blockers, the other open stories a blocker could be chosen from, the fields as the
board has them, and the analogy table the estimate comes from. The plan is what the points are
estimated against, and the review is read here rather than by a separate `gh` call. Each block
degrades to one line of its own, so a lookup that fails never costs the model the rest of the
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
    indented,
    issue_ref,
    reason,
    ref_label,
    refuse_stub,
    settings_or_error,
    step,
    usable,
)

# About three quarters of the body limit. Over it a story is usually several, and the ninety per
# cent note was printed past five stories in one evening, so the gate is here where scope is judged.
BOARDING_LIMIT = 50_000

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


def _plan_lines(number: int) -> list[str]:
    """The Plan section, which is the work the points are estimated against."""
    body = issue.view(gh.repo_slug(), number).body
    return indented(sections.get(body, "Plan", "").strip("\n").splitlines(), "no plan")


def _review_lines(number: int) -> list[str]:
    """The latest Review: entry, which is the gate this step holds."""
    entry = log.last(issue.view(gh.repo_slug(), number), "Review:")
    return indented(entry.body.splitlines() if entry is not None else [], "no review")


def _fields_block(settings: Settings | Exception, number: int) -> list[str]:
    values = fields.get_fields(usable(settings), gh.repo_slug(), number, FIELDS)
    return [f"  {name}: {'unset' if value is None else value}" for name, value in values.items()]


def _table_block(settings: Settings | Exception) -> list[str]:
    rows = analogy_rows(board.items(usable(settings)), LIMIT)
    return [TABLE_HEADER, TABLE_RULE, *rows] if rows else ["  none"]


def _could_block(settings: Settings | Exception, number: int) -> list[str]:
    """Every other open story on the board, so a blocker is chosen here and not on the board by hand."""
    rows = []
    for node in board.items(usable(settings)):
        content = node.get("content") or {}
        status = board.field_value(node, "Status", "name") or "-"
        if content.get("number") == number or status == "Done":
            continue
        rows.append(f"#{content.get('number')} {status} {content.get('title') or ''}".rstrip())
    return indented(rows, "none")


def context(args: argparse.Namespace) -> int:
    """Print the kinds, the story, the plan, the review, the blockers, the fields, and the Done stories."""
    settings = settings_or_error()
    print(_kinds_line(settings))
    print()
    block("## Story", lambda: _story_lines(args.issue))
    block("## Plan", lambda: _plan_lines(args.issue))
    block("Review:", lambda: _review_lines(args.issue))
    block("Blockers:", lambda: blockers_block(gh.repo_slug(), args.issue))
    block("Could block this story:", lambda: _could_block(settings, args.issue))
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
        raise Refusal(f"{RULE_REVIEWED} Run /deckhand:next {number}.")


def _open_issue(repo: str, number: int, here: str) -> None:
    """Refuse unless issue `number` of `repo` exists and is open; a blocker that is neither blocks nothing.

    Only the sibling read: a blocker's comments are none of this step's business, and its state is
    the half of that read this step acts on.
    """
    label = ref_label(repo, number, here)
    try:
        state = issue.sibling(repo, number)[0]
    except gh.GhError as error:
        said = reason(error)
        if _NOT_FOUND.search(said):
            raise Refusal(f"{label} does not exist") from error
        raise Refusal(f"{label} cannot be read: {said}") from error
    if state.upper() != "OPEN":
        raise Refusal(f"{label} is closed")


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


RULE_REVIEWED = "A story boards only with a Review: entry in its log."
RULE_SIZE = f"A body over {BOARDING_LIMIT} characters needs --oversized saying why it is one story."
RULES = (RULE_REVIEWED, RULE_SIZE)


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kind", required=True, help="the kind of change, one the project defines")
    parser.add_argument(
        "--oversized", metavar="WHY", help="board a body over the size gate, saying why it is one story"
    )
    parser.add_argument("--points", required=True, help="the story points, a non-negative integer")
    parser.add_argument(
        "--blocked-by",
        action="append",
        default=[],
        metavar="REF",
        help="an open issue this story waits on, owner/name#M or M for this repository; repeat it for more",
    )


@step("ready", _configure, rules=RULES)
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
    size = len(story.body.replace("\r\n", "\n"))
    if size > BOARDING_LIMIT and not args.oversized:
        raise Refusal(f"{RULE_SIZE} Body is {size} characters; board it with --oversized '<why it is one>'.")
    try:
        blockers = list(dict.fromkeys(issue_ref(value, repo) for value in args.blocked_by or []))
    except ValueError as error:
        raise Refusal(str(error)) from error
    if (repo, args.issue) in blockers:
        raise Refusal(f"#{args.issue} cannot block itself")
    for where, number in blockers:
        _open_issue(where, number, repo)
    for where, number in blockers:
        issue.add_dependency(repo, args.issue, (where, number))
        print(f"Blocked by {ref_label(where, number, repo)}")
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
    if args.oversized:  # the size was judged here, so the judgment belongs on the record here
        issue.comment(repo, args.issue, log.checked(f"Noted: boarded at {size} characters. {args.oversized}"))
        print("Logged Noted")
    return 0
