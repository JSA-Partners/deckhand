"""The build order the blocker graph implies: what can start first, and what waits on what.

The order is derived and never stored. The edges live on GitHub as issue dependencies and the fleet
reads them, so nothing here calls out, nothing here writes, and the same fleet always ranks the same.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from deckhand.fleet import Blockers, Key, Story
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
