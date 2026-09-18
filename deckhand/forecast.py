"""How long finished stories took, and how long a batch of them will take.

Nothing here reads GitHub or prints: it takes stories, their blockers and a session count, and
returns numbers. The durations are measured rather than estimated, from the timestamps the log has
always carried, so a point value selects which history is relevant instead of predicting a time.

A stall is never trimmed. The longest story in a band is in that band at the rate stalls actually
happen, and discarding it would produce exactly the optimistic estimate this exists to avoid.
"""

from __future__ import annotations

import math
import random
import statistics
from datetime import datetime

from deckhand import fleet, log

PERCENTILES = (50, 85, 95, 100)


def _stamp(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def durations(stories: list[fleet.Story]) -> dict[int | None, list[float]]:
    """Hours from `Started:` to `Pull request:` for every closed story, banded by points."""
    found: dict[int | None, list[float]] = {}
    for story in stories:
        if not story.closed:
            continue
        started = log.last(story.issue, "Started:")
        opened = log.last(story.issue, "Pull request:")
        if started is None or opened is None:
            continue
        hours = (_stamp(opened.created_at) - _stamp(started.created_at)).total_seconds() / 3600
        found.setdefault(story.points, []).append(hours)
    return {points: sorted(found[points]) for points in sorted(found, key=lambda p: (p is None, p))}


def _order(stories: list[fleet.Story], blockers: fleet.Blockers) -> list[fleet.Story]:
    return fleet._topological(stories, blockers, lambda story: (story.number, story.number, story.number))


def _waits(story: fleet.Story, blockers: fleet.Blockers, finish: dict[fleet.Key, float]) -> float:
    holds = blockers.get(story.key) or []
    return max((finish.get((where, number), 0.0) for where, number, _ in holds), default=0.0)


def floor(stories: list[fleet.Story], blockers: fleet.Blockers, sessions: int, hours: dict[int | None, float]) -> float:
    """`max(critical path, total work / sessions)`, both proven lower bounds on the makespan."""
    fallback = statistics.median(hours.values())
    duration = {story.key: hours.get(story.points, fallback) for story in stories}
    finish: dict[fleet.Key, float] = {}
    path = 0.0
    for story in _order(stories, blockers):
        end = _waits(story, blockers, finish) + duration[story.key]
        finish[story.key] = end
        path = max(path, end)
    return max(path, sum(duration.values()) / max(sessions, 1))


def _schedule(
    order: list[fleet.Story], blockers: fleet.Blockers, sessions: int, duration: dict[fleet.Key, float]
) -> float:
    """List scheduling: each story to whichever session frees up first, no earlier than its blockers finish."""
    free = [0.0] * max(sessions, 1)
    finish: dict[fleet.Key, float] = {}
    for story in order:
        session = min(range(sessions), key=lambda i: free[i])
        start = max(free[session], _waits(story, blockers, finish))
        finish[story.key] = free[session] = start + duration[story.key]
    return max(finish.values(), default=0.0)


def _percentile(sorted_values: list[float], p: int) -> float:
    index = math.ceil(p / 100 * len(sorted_values)) - 1
    return sorted_values[min(len(sorted_values) - 1, max(0, index))]


def simulate(
    stories: list[fleet.Story],
    blockers: fleet.Blockers,
    sessions: int,
    samples: dict[int | None, list[float]],
    runs: int = 10_000,
    seed: int | None = None,
) -> dict[int, float]:
    """The makespan at `PERCENTILES`, from `runs` schedules drawn from `samples`."""
    rng = random.Random(seed)
    pool = [value for band in samples.values() for value in band]
    order = _order(stories, blockers)

    def _draw() -> dict[fleet.Key, float]:
        return {story.key: rng.choice(samples.get(story.points) or pool) for story in stories}

    makespans = sorted(_schedule(order, blockers, sessions, _draw()) for _ in range(runs))
    return {p: _percentile(makespans, p) for p in PERCENTILES}
