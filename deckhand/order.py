"""The build order the blocker graph implies: what can start first, what waits on what, and its table.

The order is derived and never stored. The edges live on GitHub as issue dependencies and the fleet
reads them, so nothing here calls out, nothing here writes, and the same fleet always ranks the same.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from deckhand import columns, sessions
from deckhand.fleet import Blockers, Fleet, Key, Story, touched
from deckhand.step import ref_label


@dataclass(frozen=True)
class Ranked:
    """One story in the build order, with the reason it sits where it does."""

    story: Story
    why: str


def downstream(start: Key, waiting: dict[Key, set[Key]]) -> set[Key]:
    """Every story that transitively waits on `start`; a cycle counts each member once and stops."""
    found: set[Key] = set()
    stack = list(waiting.get(start, ()))
    while stack:
        node = stack.pop()
        if node in found:
            continue
        found.add(node)
        stack.extend(waiting.get(node, ()))
    found.discard(start)
    return found


def waiting(blockers: Blockers) -> dict[Key, set[Key]]:
    """The blockers map turned around: who is waiting on each story."""
    found: dict[Key, set[Key]] = {}
    for key, holds in blockers.items():
        for where, number, _ in holds:
            found.setdefault((where, number), set()).add(key)
    return found


def topological(held: list[Story], blockers: Blockers, rank: Callable[[Story], tuple[int, int, int]]) -> list[Story]:
    """`held` placed so nothing precedes a blocker that is also in `held`; a cycle falls back to rank."""
    remaining = list(held)
    left = {story.key for story in remaining}
    placed: list[Story] = []
    while remaining:
        free = [s for s in remaining if not {(w, n) for w, n, _ in blockers.get(s.key) or []} & left]
        best = min(free or remaining, key=rank)
        placed.append(best)
        remaining.remove(best)
        left.discard(best.key)
    return placed


def _why(story: Story, blockers: list[tuple[str, int, str]], below: set[Key], points: dict[Key, int]) -> str:
    if blockers:
        named = ", ".join(ref_label(where, number, story.repo) for where, number, _ in blockers)
        return f"waits on {named}"
    if not below:
        return "ready, unblocks nothing"
    count = len(below)
    stories = "story" if count == 1 else "stories"
    return f"ready, unblocks {count} {stories}, {sum(points.get(key, 0) for key in below)} pts"


def ranked(backlog: list[Story], blockers: Blockers) -> list[Ranked]:
    """The Backlog ranked: what can start first, by the work it frees, then what waits.

    Weight is the points of everything transitively waiting on a story rather than a count of its
    neighbors, so the story that frees the longest chain leads. Equal weight breaks toward fewer
    points, so a cheap unblocker goes first, and equal again breaks by number so two runs agree.
    """
    waits = waiting(blockers)
    points = {story.key: story.points or 0 for story in backlog}
    ready = [story for story in backlog if not blockers.get(story.key)]
    held = [story for story in backlog if blockers.get(story.key)]
    weights = {story.key: downstream(story.key, waits) for story in backlog}

    def rank(story: Story) -> tuple[int, int, int]:
        below = weights[story.key]
        return (-sum(points.get(key, 0) for key in below), story.points or 0, story.number)

    placed = sorted(ready, key=rank) + topological(held, blockers, rank)
    return [
        Ranked(story=story, why=_why(story, blockers.get(story.key) or [], weights[story.key], points))
        for story in placed
    ]


def _name(repo: str) -> str:
    return repo.partition("/")[2] or repo


def backlog(read: Fleet) -> list[Ranked]:
    """The Backlog stories deckhand owns in this reading, ranked; an issue opened by hand is not one."""
    held = [story for story in read.stories if story.status == columns.BACKLOG and touched(story)]
    return ranked(held, read.blockers)


def rows(read: Fleet) -> list[str]:
    """The order table, one row per Backlog story in its rank."""
    placed = backlog(read)
    if not placed:
        return ["  nothing in Backlog"]
    table = ["| Rank | # | Repo | Pts | Why |", "| --- | --- | --- | --- | --- |"]
    for place, row in enumerate(placed, start=1):
        points = "-" if row.story.points is None else str(row.story.points)
        table.append(f"| {place} | {row.story.number} | {_name(row.story.repo)} | {points} | {row.why} |")
    return table


def _head(read: Fleet, repo: str) -> str | None:
    """The open story at the top of this repository's waiting chains, and how much it holds up.

    A blocker that is not in Backlog is nowhere in the order table, so a repository whose whole
    Backlog waits has nothing to show without it.
    """
    queued = {story.key for story in read.stories if story.status == columns.BACKLOG and touched(story)}
    seen: set[Key] = set()
    roots: set[Key] = set()
    stack = [(w, n) for key, holds in read.blockers.items() if key[0] == repo and key in queued for w, n, _ in holds]
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        above = [(w, n) for w, n, _ in read.blockers.get(key) or []]
        if above:
            stack.extend(above)
        elif key not in queued:
            roots.add(key)
    if not roots:
        return None
    waits = waiting(read.blockers)
    best = max(sorted(roots), key=lambda key: len(downstream(key, waits)))
    count = len(downstream(best, waits))
    story = next((row for row in read.stories if row.key == best), None)
    status = story.status if story and story.status else "off the board"
    stories = "story" if count == 1 else "stories"
    return f"{ref_label(best[0], best[1], repo)} is {status}, unblocking {count} {stories}"


def next_line(read: Fleet, pulses: list[sessions.Pulse]) -> str:
    """Each repository's top-ranked story in the ranked order, and whether a session is open to run it."""
    free = {beat.repo for beat in pulses if beat.story == sessions.FREE}
    seen: dict[str, str] = {}
    held: set[str] = set()
    for row in backlog(read):
        if row.why.startswith("waits"):
            held.add(row.story.repo)
            continue
        if row.story.repo in seen:
            continue
        where = "a session is free" if row.story.repo in free else "no session open"
        seen[row.story.repo] = f"{_name(row.story.repo)} {row.story.number} ({where})"
    for repo in sorted(held - set(seen)):
        head = _head(read, repo)
        if head is not None:
            seen[repo] = f"{_name(repo)}: {head}"
    return "Next per repository: " + (", ".join(seen.values()) if seen else "nothing ready")
