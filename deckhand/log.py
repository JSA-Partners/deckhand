"""The issue's log: one comment per decision, each opening with a prefix the process reads back.

The body of an issue is the story; the comments under it are what happened to it, in order, written
by the process. Each one opens with one of `PREFIXES`, which is how `next` reads where a story is and
how anyone reading later sees what was decided and why; a person's comment carries no prefix and is
skipped. `deckhand log N "<Prefix: text>"` is the one way a session writes one, so nothing in a skill
runs `gh` itself.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from deckhand import gh, issue
from deckhand.cli import command
from deckhand.step import Refusal, issue_number

PREFIXES = (
    "Drafted:",
    "Review:",
    "Amended:",
    "Started:",
    "Deviation:",
    "Split:",
    "Reviewed:",  # the commit sha first, then a line on the pass
    "Pull request:",
    "After the merge:",
)


@dataclass(frozen=True)
class Entry:
    """One log comment: its prefix, the rest of its first line, the whole body, and when it was posted."""

    prefix: str
    text: str
    body: str
    created_at: str


def _first(text: str) -> str:
    """The first non-blank line of `text`, stripped; the one line the log reads a prefix from."""
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _split(text: str) -> tuple[str, str] | None:
    """`(prefix, rest of the first non-blank line)`, or None when no prefix opens it with text after."""
    first = _first(text)
    for prefix in PREFIXES:
        if first.startswith(prefix) and first[len(prefix) :].strip():
            return prefix, first[len(prefix) :].strip()
    return None


def parse(comment: issue.Comment) -> Entry | None:
    """The comment as an entry, or None when it does not open with a prefix followed by text."""
    found = _split(comment.body)
    return Entry(*found, comment.body, comment.created_at) if found is not None else None


def entries(story: issue.Issue) -> list[Entry]:
    """Every log entry on the story, oldest first; a person's comment is not one."""
    return [entry for entry in (parse(comment) for comment in story.comments) if entry is not None]


def last(story: issue.Issue, prefix: str) -> Entry | None:
    """The latest entry with `prefix`, or None."""
    return next((entry for entry in reversed(entries(story)) if entry.prefix == prefix), None)


def since(story: issue.Issue, prefix: str) -> list[Entry]:
    """Every entry after the latest one with `prefix`; all of them when there is none."""
    found = entries(story)
    for index, entry in reversed(list(enumerate(found))):
        if entry.prefix == prefix:
            return found[index + 1 :]
    return found


def checked(text: str) -> str:
    """`text` when the log will read it back as an entry, else a refusal saying what is missing.

    The same first line `parse` reads, so the command never posts what `entries` would skip.
    """
    if _split(text) is not None:
        return text
    prefix = next((p for p in PREFIXES if _first(text).startswith(p)), None)
    if prefix is None:
        raise Refusal(f"the text must open with one of: {', '.join(PREFIXES)}")
    raise Refusal(f"nothing after {prefix}; say what happened")


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("issue", type=issue_number, help="the issue number")
    parser.add_argument("text", help="the entry, opening with its prefix, posted as it is written")


@command("log", _configure)
def post(args: argparse.Namespace) -> int:
    """Post one log entry on the issue, exactly as it was written."""
    print(issue.comment(gh.repo_slug(), args.issue, checked(args.text)))
    return 0
