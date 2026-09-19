"""The ready step: a reviewed story moves to Backlog with its kind, its points, and its blockers.

`context` prints the rules `apply` holds, the story and its plan, the Review: entry that is the
gate, the open blockers, the other open stories a blocker could be chosen from, the fields as the
board has them, and the reference stories the estimate is compared against. The plan is what the points are
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
import time

from deckhand import board, config, fields, fleet, forecast, gh, issue, log, sections
from deckhand.config import Settings
from deckhand.step import (
    Refusal,
    block,
    blockers_block,
    indented,
    issue_ref,
    open_issue,
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

FIELDS = ("Kind", "Story Points")
REFERENCES = 3  # per point value: enough to compare against, few enough to read
SETTLE = 2.0
TABLE_HEADER = "| Pts | # | Repo | Title | Hours |"
TABLE_RULE = "| --- | --- | --- | --- | --- |"


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


# --- the reference stories -------------------------------------------------


def reference_rows(stories: list[fleet.Story]) -> list[str]:
    """The most recent finished stories of each point value, with the hours each took."""
    measured = []
    for story in stories:
        took = forecast.hours(story) if story.closed and story.points is not None else None
        if took is not None:
            opened = log.last(story.issue, "Pull request:")
            measured.append((opened.created_at if opened else "", story, took))
    measured.sort(key=lambda row: row[0], reverse=True)
    rows = []
    for points in sorted({story.points for _, story, _ in measured}):
        for _, story, took in [row for row in measured if row[1].points == points][:REFERENCES]:
            repo = story.repo.partition("/")[2] or story.repo
            title = story.title.replace("|", "\\|")
            rows.append(f"| {points} | {story.number} | {repo} | {title} | {took:.1f} |")
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
    rows = reference_rows(fleet.load(usable(settings)))
    return [f"  {forecast.POINT}", *([TABLE_HEADER, TABLE_RULE, *rows] if rows else ["  none"])]


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
    """Print the kinds, the fields and reference stories to size by, then the story, plan, review, and blockers."""
    settings = settings_or_error()
    print(_kinds_line(settings))
    print()
    block("Fields:", lambda: _fields_block(settings, args.issue))
    block("Reference stories:", lambda: _table_block(settings))
    block("## Story", lambda: _story_lines(args.issue))
    block("## Plan", lambda: _plan_lines(args.issue))
    block("Review:", lambda: _review_lines(args.issue))
    block("Blockers:", lambda: blockers_block(gh.repo_slug(), args.issue))
    block("Could block this story:", lambda: _could_block(settings, args.issue))
    return 0


# --- apply ------------------------------------------------------------------


MOST_POINTS = 3  # the largest single outcome; anything bigger is several stories


def _points(value: str) -> int:
    if not (value.isascii() and value.isdigit()):
        raise Refusal(f"points must be a whole number, got {value!r}")
    points = int(value)
    if points < 1:
        raise Refusal("points start at 1")
    if points > MOST_POINTS:
        raise Refusal(
            f"more than {MOST_POINTS} points is more than one story; split it with the Review section's split"
        )
    return points


def _reviewed(story: issue.Issue, number: int) -> None:
    """Refuse unless the story carries a `Review:` entry, which is the only gate this step holds."""
    if log.last(story, "Review:") is None:
        raise Refusal(f"{RULE_REVIEWED} Run /deckhand:next {number}.")


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
    parser.add_argument("--points", required=True, help="the story points, 1 to 3")
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
        open_issue(where, number, repo)
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
