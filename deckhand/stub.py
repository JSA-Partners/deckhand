"""The stub: a plain issue whose body is a feature's requirements and the stories it was split into.

A stub is not a story. It carries no section headings, no board item, and no plan; `new` turns one
into a story by rewriting its body, and every other step refuses it on sight. `is_stub` is the whole
of that test, so nothing else has to know what a stub looks like.

Everything here is pure text. `parse_split` reads what the session wrote and is strict, because a
bad line is a bad split and the user has to see which line; `read` reads a body GitHub already holds
and is forgiving, because a stub the user edited by hand is still worth printing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

STUB_HEADING = "## Requirements"
STORIES_HEADING = "## Stories"


def skeleton() -> str:
    """The empty shape a park file takes: the requirements alone, because a park has no stories yet."""
    return "\n\n".join([STUB_HEADING, "<what the feature needs, in full>"])


_BULLET = re.compile(r"^-\s+(.*)$")
_NUMBERED = re.compile(r"^[0-9]+\.\s+(.*)$")
_AFTER = re.compile(r"\s*\(after\s+([0-9]+(?:\s*,\s*[0-9]+)*)\)\s*$")
_ISSUE = re.compile(r"^#([0-9]+)\s+(.*)$")
_REPO = re.compile(r"^([^\s:/]+/[^\s:/]+):\s+(.*)$")  # `owner/name: ` before the title, the story's repository


@dataclass(frozen=True)
class Entry:
    """One story of a feature: its title, its one sentence, what it waits on, and its issue."""

    title: str
    sentence: str
    after: tuple[int, ...]  # 1-based positions in the list, always earlier than this entry's own
    number: int | None = None  # the issue number once it has been created
    repo: str | None = None  # the repository the story belongs to when it is not this one


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
    repo = None
    home = _REPO.match(rest)
    if home is not None:
        repo, rest = home.group(1), home.group(2)
    number = None
    issue = _ISSUE.match(rest)
    if issue is not None:
        number, rest = int(issue.group(1)), issue.group(2)
    parts = [part.strip() for part in rest.split("|")]
    if len(parts) > 2:
        raise ValueError("a title or sentence cannot contain '|'")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("expected '<title> | <sentence>'")
    return Entry(title=parts[0], sentence=parts[1], after=after, number=number, repo=repo)


def _resolved(stories: list[object]) -> list[Entry]:
    """Each story as an `Entry`, with `after` resolved from keys to the positions `render` writes.

    The keys are the authored form and never leave here: a person counting positions by hand is what
    an insertion silently shifts, and deckhand generates the positions it stores.
    """
    at: dict[str, int] = {}
    entries: list[Entry] = []
    for position, story in enumerate(stories, start=1):
        where = f"story {position}"
        if not isinstance(story, dict):
            raise ValueError(f"{where}: not an object")
        for field in ("key", "title", "sentence"):
            if not str(story.get(field) or "").strip():
                raise ValueError(f"{where}: needs a {field}")
        key = str(story["key"]).strip()
        if key in at:
            raise ValueError(f"{where}: '{key}' is already the key of an earlier story")
        after = story.get("after") or []
        if not isinstance(after, list):
            raise ValueError(f"{where}: after is a list of keys")
        seen: set[str] = set()
        positions: list[int] = []
        for named in after:
            if named in seen:
                raise ValueError(f"{where}: after names '{named}' twice")
            seen.add(str(named))
            if named not in at:
                raise ValueError(f"{where}: after names '{named}', which is not a story defined before it")
            positions.append(at[str(named)])
        at[key] = position
        repo = str(story["repo"]).strip() if story.get("repo") else None
        entries.append(
            Entry(
                title=str(story["title"]).strip(),
                sentence=str(story["sentence"]).strip(),
                after=tuple(positions),
                repo=repo,
            )
        )
    return entries


def parse_split(text: str) -> tuple[str, list[Entry]]:
    """The requirements and the stories of a split document, or a ValueError naming what is wrong."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"line {error.lineno} column {error.colno}: {error.msg}") from None
    if not isinstance(document, dict) or document.get("kind") != "split":
        raise ValueError('opens with {"kind": "split", "requirements": "...", "stories": [...]}')
    requirements = document.get("requirements")
    if not isinstance(requirements, str) or not requirements.strip():
        raise ValueError("'requirements' says what the feature needs, in full")
    stories = document.get("stories")
    if not isinstance(stories, list) or not stories:
        raise ValueError("'stories' lists one story per issue the split opens")
    return requirements.strip("\n").rstrip(), _resolved(stories)


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
    """The shape `parse_split` reads, in the one place that owns it.

    A context shows the session this, so the wording cannot drift from what the parser accepts.
    """
    return json.dumps(
        {
            "kind": "split",
            "requirements": "<what the feature needs, in full>",
            "stories": [
                {"key": "<name>", "title": "<title>", "sentence": "<one sentence>"},
                {
                    "key": "<name>",
                    "title": "<title>",
                    "sentence": "<one sentence>",
                    "repo": "<owner/name, when it belongs elsewhere>",
                    "after": ["<the key of a story above>"],
                },
            ],
        },
        indent=2,
    )


def _rendered(entry: Entry) -> str:
    """One entry's own text, without its position: what `_entry` reads back."""
    home = f"{entry.repo}: " if entry.repo else ""
    issue = f"#{entry.number} " if entry.number is not None else ""
    after = f" (after {', '.join(str(position) for position in entry.after)})" if entry.after else ""
    return f"{home}{issue}{entry.title} | {entry.sentence}{after}"


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
