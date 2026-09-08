"""The stub: a plain issue whose body is a feature's requirements and the stories it was split into.

A stub is not a story. It carries no section headings, no board item, and no plan; `new` turns one
into a story by rewriting its body, and every other step refuses it on sight. `is_stub` is the whole
of that test, so nothing else has to know what a stub looks like.

Everything here is pure text. `parse_split` reads what the session wrote and is strict, because a
bad line is a bad split and the user has to see which line; `read` reads a body GitHub already holds
and is forgiving, because a stub the user edited by hand is still worth printing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STUB_HEADING = "## Requirements"
STORIES_HEADING = "## Stories"

_BULLET = re.compile(r"^-\s+(.*)$")
_NUMBERED = re.compile(r"^[0-9]+\.\s+(.*)$")
_AFTER = re.compile(r"\s*\(after\s+([0-9]+(?:\s*,\s*[0-9]+)*)\)\s*$")
_ISSUE = re.compile(r"^#([0-9]+)\s+(.*)$")


@dataclass(frozen=True)
class Entry:
    """One story of a feature: its title, its one sentence, what it waits on, and its issue."""

    title: str
    sentence: str
    after: tuple[int, ...]  # 1-based positions in the list, always earlier than this entry's own
    number: int | None = None  # the issue number once it has been created


def _lines(text: str) -> list[str]:
    """`text` as lines, with every line ending an editor might have written normalised first."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def is_stub(body: str) -> bool:
    """Whether `body` is a stub: its first non-blank line is the requirements heading."""
    for line in _lines(body):
        if line.strip():
            return line.strip() == STUB_HEADING
    return False


def _after_positions(text: str) -> tuple[str, tuple[int, ...]]:
    """`text` without its trailing `(after 1, 2)`, and the positions that suffix named."""
    match = _AFTER.search(text)
    if match is None:
        return text.rstrip(), ()
    return text[: match.start()].rstrip(), tuple(int(position) for position in match.group(1).split(","))


def _entry(text: str) -> Entry:
    """One entry from `[#<number> ]<title> | <sentence>[ (after 1, 2)]`, without its list marker.

    A pipe, not a colon, as a review finding uses: a title reads as prose, and a colon inside one
    would take half of it into the sentence and mistitle the issue with nobody the wiser.
    """
    rest, after = _after_positions(text.strip())
    number = None
    issue = _ISSUE.match(rest)
    if issue is not None:
        number, rest = int(issue.group(1)), issue.group(2)
    parts = [part.strip() for part in rest.split("|")]
    if len(parts) > 2:
        raise ValueError("a title or sentence cannot contain '|'")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("expected '<title> | <sentence>'")
    return Entry(title=parts[0], sentence=parts[1], after=after, number=number)


def _checked(numbered: list[tuple[int, Entry]]) -> list[Entry]:
    """Every entry, once each `after` names an entry that exists and comes before it."""
    total = len(numbered)
    for position, (line, entry) in enumerate(numbered, start=1):
        seen: set[int] = set()
        for after in entry.after:
            if after in seen:
                raise ValueError(f"line {line}: after {after} is named twice")
            seen.add(after)
            if not 1 <= after <= total:
                raise ValueError(f"line {line}: after {after} names no story; there are {total}")
            if after >= position:
                raise ValueError(f"line {line}: after {after} names this story or a later one")
    return [entry for _, entry in numbered]


def parse_split(text: str) -> tuple[str, list[Entry]]:
    """The requirements and the stories of a split file, or a ValueError naming the line at fault."""
    lines = _lines(text)
    first = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first is None or lines[first].strip() != STUB_HEADING:
        raise ValueError(f"line {(0 if first is None else first) + 1}: expected {STUB_HEADING}")
    stories = next((index for index in range(first + 1, len(lines)) if lines[index].strip() == STORIES_HEADING), None)
    if stories is None:
        raise ValueError(f"line {len(lines)}: expected {STORIES_HEADING}")
    requirements = "\n".join(lines[first + 1 : stories]).strip("\n").rstrip()
    if not requirements.strip():
        raise ValueError(f"line {first + 1}: {STUB_HEADING} has no text")
    numbered: list[tuple[int, Entry]] = []
    for line_number, line in enumerate(lines[stories + 1 :], start=stories + 2):
        if not line.strip():
            continue
        bullet = _BULLET.match(line.strip())
        if bullet is None:
            raise ValueError(f"line {line_number}: expected a bullet '- <title> | <sentence>'")
        try:
            numbered.append((line_number, _entry(bullet.group(1))))
        except ValueError as error:
            raise ValueError(f"line {line_number}: {error}") from error
    if not numbered:
        raise ValueError(f"line {stories + 1}: {STORIES_HEADING} lists no stories; write one bullet per story")
    return requirements, _checked(numbered)


def lists_stories(text: str) -> bool:
    """Whether `text` already names stories: a bullet or a numbered entry under the Stories heading.

    Both forms, because the same text can arrive as the bullets a session wrote or as the numbered
    list `render` writes back, and either way the feature has been split.
    """
    lines = _lines(text)
    start = next((index for index, line in enumerate(lines) if line.strip() == STORIES_HEADING), None)
    if start is None:
        return False
    return any(_BULLET.match(line.strip()) or _NUMBERED.match(line.strip()) for line in lines[start + 1 :])


def split_skeleton() -> str:
    """The empty split file a session writes a feature into: both headings and one example bullet.

    The shape `parse_split` reads, in the one place that owns it, so a context can show the session
    what to write without the wording drifting from what the parser accepts.
    """
    return "\n\n".join([STUB_HEADING, STORIES_HEADING, "- <title> | <one sentence> (after 1)"])


def _rendered(entry: Entry) -> str:
    """One entry's own text, without its position: what `_entry` reads back."""
    issue = f"#{entry.number} " if entry.number is not None else ""
    after = f" (after {', '.join(str(position) for position in entry.after)})" if entry.after else ""
    return f"{issue}{entry.title} | {entry.sentence}{after}"


def render(requirements: str, entries: list[Entry]) -> str:
    """The stub body every stub of one feature carries: the requirements and the numbered stories."""
    lines = [STUB_HEADING, "", requirements.rstrip(), "", STORIES_HEADING]
    if entries:
        lines.append("")
        lines += [f"{position}. {_rendered(entry)}" for position, entry in enumerate(entries, start=1)]
    return "\n".join(lines) + "\n"


def read(body: str) -> tuple[str, list[Entry]]:
    """What `render` wrote: the requirements and the entries; a parked feature reads as no entries.

    A line under Stories that is not an entry is skipped rather than refused: this reads a body
    GitHub already holds, and a hand-edited stub is still worth showing the session.
    """
    lines = _lines(body)
    start = next((index for index, line in enumerate(lines) if line.strip()), len(lines))
    if start < len(lines) and lines[start].strip() == STUB_HEADING:
        start += 1
    stories = next((index for index in range(start, len(lines)) if lines[index].strip() == STORIES_HEADING), len(lines))
    requirements = "\n".join(lines[start:stories]).strip("\n").rstrip()
    entries: list[Entry] = []
    for line in lines[stories + 1 :]:
        numbered = _NUMBERED.match(line.strip())
        if numbered is None:
            continue
        try:
            entries.append(_entry(numbered.group(1)))
        except ValueError:
            continue
    return requirements, entries
