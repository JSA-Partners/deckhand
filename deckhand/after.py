"""The after step: what a merged story still owes, ticked one item at a time, until it reaches Done.

The pull request merges, GitHub closes the issue, and no step runs at that moment, so a story that
has landed sits in the column `finish` left it in. This step is that moment: the Plan's closing
"After the merge" block says what is owed once the code is on main, and each item is ticked here as
it is done. The boxes are the record, so the column follows them: anything left unticked is
Verification, and the last tick is Done. A plan that asked for nothing is Done as soon as the issue
closes.
"""

from __future__ import annotations

import argparse
import re

from deckhand import columns, fields, gh, invoke, issue, log, sections
from deckhand.step import Refusal, block, indented, resolved_settings, step

OWED = "After the merge:"
_BULLET = re.compile(r"^- (?:\[[ xX]\]\s+)?")


def _lines(items: list[tuple[str, bool]]) -> list[str]:
    """Each item numbered from one, with its box as the story carries it."""
    return [f"{number}. [{'x' if ticked else ' '}] {text}" for number, (text, ticked) in enumerate(items, 1)]


def _next_unticked(items: list[tuple[str, bool]]) -> int | None:
    """The position of the first item still owed, or None when the story owes nothing."""
    return next((number for number, (_, ticked) in enumerate(items, 1) if not ticked), None)


def context(args: argparse.Namespace) -> int:
    """Print the item the next apply ticks, then the story and what it still owes after the merge."""
    story = issue.view(gh.repo_slug(), args.issue)
    items = sections.after_merge_items(story.body)
    position = _next_unticked(items)
    parts = ("--item", str(position)) if position is not None else ()
    print(invoke.apply_line("after", str(args.issue), *parts))
    print()
    print(f"Title: {story.title}")
    print(f"Issue: {story.url}")
    print()
    block(OWED, lambda: indented(_lines(items)))
    return 0


def _ticked(body: str, position: int) -> str:
    """The body with item `position`'s box ticked, every other line of the plan as it was."""
    plan = sections.get(body, "Plan", "")
    lines = plan.split("\n")
    start = next(number for number, line in enumerate(lines) if sections.AFTER_MERGE.match(line))
    bullets = [number for number in range(start + 1, len(lines)) if lines[number].startswith("- ")]
    index = bullets[position - 1]
    lines[index] = _BULLET.sub("- [x] ", lines[index], count=1)
    return sections.replace(body, "Plan", "\n".join(lines))


def _tick(repo: str, number: int, story: issue.Issue, items: list[tuple[str, bool]], position: int) -> str:
    """Tick item `position` on the issue and log it; returns the body the column is then read from."""
    if not 1 <= position <= len(items):
        owed = "item" if len(items) == 1 else "items"
        raise Refusal(f"#{number} has {len(items)} after-the-merge {owed}; there is no item {position}")
    text, already = items[position - 1]
    if already:
        raise Refusal(f"item {position} of #{number} is already ticked: {text}")
    body = _ticked(story.body, position)
    entry = log.checked(f"After the merge: {text}")
    # The boxes in this block are this step's to write, which is why the freeze `amend` holds does
    # not reach them: no other command edits the body of a closed story.
    issue.update_body(repo, number, body)
    print(f"Ticked item {position}: {text}", flush=True)
    issue.comment(repo, number, entry)
    print("Logged After the merge")
    return body


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--item", type=int, metavar="N", help="the after-the-merge item to tick, counting from one")


@step("after", _configure)
def apply(args: argparse.Namespace) -> int:
    """Tick one after-the-merge item on a merged story, and set the column the remaining boxes ask for."""
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    if story.state.upper() != "CLOSED":
        raise Refusal(f"#{args.issue} is still open; this step runs once its pull request has merged")
    items = sections.after_merge_items(story.body)
    settings = resolved_settings()
    if not items:
        print(fields.set_field(settings, repo, args.issue, "Status", columns.DONE))
        return 0
    body = _tick(repo, args.issue, story, items, args.item) if args.item is not None else story.body
    left = [text for text, ticked in sections.after_merge_items(body) if not ticked]
    print(fields.set_field(settings, repo, args.issue, "Status", columns.VERIFICATION if left else columns.DONE))
    return 0
