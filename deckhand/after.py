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
from dataclasses import replace

from deckhand import columns, config, fields, gh, invoke, issue, log, sections
from deckhand.step import Refusal, block, indented, reason, resolved_settings, step

OWED = "After the merge:"
_BULLET = re.compile(r"^- (?:\[[ xX]\]\s+)?")


def _lines(items: list[tuple[str, bool]]) -> list[str]:
    """Each item numbered from one, with its box as the story carries it."""
    return [f"{number}. [{'x' if ticked else ' '}] {text}" for number, (text, ticked) in enumerate(items, 1)]


def _next_unticked(items: list[tuple[str, bool]]) -> int | None:
    """The position of the first item still owed, or None when the story owes nothing."""
    return next((number for number, (_, ticked) in enumerate(items, 1) if not ticked), None)


def _column(repo: str, number: int) -> str | None:
    """The story's column, or None when the board cannot say."""
    try:
        return fields.get_field(config.load(), repo, number, "Status")
    except Exception:
        return None


def context(args: argparse.Namespace) -> int:
    """Print the apply to run next, then the story and what it still owes after the merge.

    A merged story still in an earlier column leads with the bare apply, because that is what moves
    it, and a session that only reads the items here would otherwise leave it where it was.
    """
    repo = gh.repo_slug()
    try:
        story = issue.view(repo, args.issue)
    except Exception as error:  # the apply is still worth naming without the story
        print(invoke.apply_line("after", str(args.issue)))
        print()
        print(f"Issue: #{args.issue} could not be read: {reason(error)}")
        return 0
    items = sections.after_merge_items(story.body)
    position = _next_unticked(items)
    tick = ("--item", str(position)) if position is not None else ()
    column = _column(repo, args.issue) if story.state.upper() == "CLOSED" else None
    if column is not None and column not in (columns.VERIFICATION, columns.DONE):
        print(invoke.apply_line("after", str(args.issue)))
        if tick:
            print(invoke.then_line("after", str(args.issue), *tick))
    else:
        print(invoke.apply_line("after", str(args.issue), *tick))
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


def _added(body: str, text: str) -> str:
    """The body with `- [ ] text` after the last After the merge item, opening the block when the Plan has none."""
    lines = sections.get(body, "Plan", "").rstrip("\n").split("\n")
    start = next((number for number, line in enumerate(lines) if sections.AFTER_MERGE.match(line)), None)
    if start is None:
        lines += ["", "### After the merge", ""]
        start = len(lines) - 1
    bullets = [number for number in range(start + 1, len(lines)) if lines[number].startswith("- ")]
    at = (bullets[-1] if bullets else start) + 1
    # A wrapped item goes on in indented lines, which belong to it, so the new item comes after them.
    while bullets and at < len(lines) and lines[at][:1].isspace() and lines[at].strip():
        at += 1
    lines.insert(at, f"- [ ] {text}")
    return sections.replace(body, "Plan", "\n".join(lines) + "\n")


def _dropped(body: str, position: int) -> str:
    """The body without item `position` and the indented lines that go on from it, the rest as it was."""
    plan = sections.get(body, "Plan", "")
    lines = plan.split("\n")
    start = next(number for number, line in enumerate(lines) if sections.AFTER_MERGE.match(line))
    bullets = [number for number in range(start + 1, len(lines)) if lines[number].startswith("- ")]
    first = bullets[position - 1]
    end = first + 1
    while end < len(lines) and lines[end][:1].isspace() and lines[end].strip():
        end += 1
    return sections.replace(body, "Plan", "\n".join(lines[:first] + lines[end:]))


def _drop(repo: str, number: int, story: issue.Issue, items: list[tuple[str, bool]], position: int, note: str) -> str:
    """Take unticked item `position` off the list and log where it went; returns the body the column is read from."""
    text, ticked = _owned(number, items, position)
    if ticked:
        raise Refusal(f"item {position} of #{number} is ticked; a done item stays on the record")
    body = _dropped(story.body, position)
    entry = log.checked(f"After the merge: dropped item {position}: {text}. {note}")
    issue.update_body(repo, number, body)
    print(f"Dropped item {position}: {text}", flush=True)
    issue.comment(repo, number, entry)
    print("Logged After the merge")
    return body


def _owned(number: int, items: list[tuple[str, bool]], position: int) -> tuple[str, bool]:
    """Item `position` as `(text, ticked)`, refused when the list has no such item."""
    if not 1 <= position <= len(items):
        owed = "item" if len(items) == 1 else "items"
        raise Refusal(f"#{number} has {len(items)} after-the-merge {owed}; there is no item {position}")
    return items[position - 1]


def _add(repo: str, number: int, story: issue.Issue, text: str) -> str:
    """Append one item to the story's After the merge list and log it; returns the body the column is read from."""
    body = _added(story.body, text)
    entry = log.checked(f"Noted: after the merge item added: {text}")
    issue.update_body(repo, number, body)
    print(f"Added item {len(sections.after_merge_items(body))}: {text}", flush=True)
    issue.comment(repo, number, entry)
    print("Logged Noted")
    return body


def _tick(repo: str, number: int, story: issue.Issue, items: list[tuple[str, bool]], position: int) -> str:
    """Tick item `position` on the issue and log it; returns the body the column is then read from."""
    text, already = _owned(number, items, position)
    if already:
        raise Refusal(f"item {position} of #{number} is already ticked: {text}")
    body = _ticked(story.body, position)
    entry = log.checked(f"After the merge: {text}")
    # The boxes in this block are this step's to write, which is why the freeze `amend` holds does
    # not reach them: only this step, ticking or adding, edits the body of a closed story.
    issue.update_body(repo, number, body)
    print(f"Ticked item {position}: {text}", flush=True)
    issue.comment(repo, number, entry)
    print("Logged After the merge")
    return body


def _configure(parser: argparse.ArgumentParser) -> None:
    act = parser.add_mutually_exclusive_group()
    act.add_argument("--item", type=int, metavar="N", help="the after-the-merge item to tick, counting from one")
    act.add_argument("--add", metavar="TEXT", help="an after-the-merge item to append to a merged story")
    act.add_argument("--drop", type=int, metavar="N", help="an unticked item to take off the list, with --note")
    parser.add_argument("--note", metavar="TEXT", help="with --drop: where the item went")


@step("after", _configure)
def apply(args: argparse.Namespace) -> int:
    """Tick one after-the-merge item on a merged story, and set the column the remaining boxes ask for."""
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    if args.add is not None and (not args.add.strip() or "\n" in args.add):
        raise Refusal("--add takes the item as one line of text")
    if (args.drop is None) != (args.note is None or not args.note.strip()):
        raise Refusal("--drop takes --note, saying where the item went; --note goes only with --drop")
    if story.state.upper() != "CLOSED" and args.add is not None:
        raise Refusal(f"#{args.issue} is still open; change its plan with amend before it starts, or log a Deviation")
    if story.state.upper() != "CLOSED":
        raise Refusal(f"#{args.issue} is still open; this step runs once its pull request has merged")
    settings = resolved_settings()
    if args.add is not None:
        story = replace(story, body=_add(repo, args.issue, story, args.add))
    items = sections.after_merge_items(story.body)
    if not items:
        print(fields.set_field(settings, repo, args.issue, "Status", columns.DONE))
        return 0
    if args.drop is not None:
        body = _drop(repo, args.issue, story, items, args.drop, " ".join(args.note.split()))
    elif args.item is not None:
        body = _tick(repo, args.issue, story, items, args.item)
    else:
        body = story.body
    left = [text for text, ticked in sections.after_merge_items(body) if not ticked]
    print(fields.set_field(settings, repo, args.issue, "Status", columns.VERIFICATION if left else columns.DONE))
    return 0
