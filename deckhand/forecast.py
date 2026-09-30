"""How long finished stories took, and how long a batch of them will take.

Nothing here reads GitHub or prints: it takes stories, their blockers and a session count, and
returns numbers and the lines of the captain's Forecast block. The durations are measured rather
than estimated, from the timestamps the log has always carried, so a point value selects which
history is relevant instead of predicting a time.

A stall is never trimmed. The longest story in a band is in that band at the rate stalls actually
happen, and discarding it would produce exactly the optimistic estimate this exists to avoid.
"""

from __future__ import annotations

import math
import random
import statistics
from datetime import datetime

from deckhand import fleet, log, order

PERCENTILES = (50, 85, 95, 100)
THIN = 10  # samples in a band below which a percentile is a fit to noise, so the worst run is used
HOURS_A_DAY = 24.0  # the durations are elapsed wall clock, including the hours a story waited
POINT = "A point groups stories that take about as long as each other. It is not hours."


def _stamp(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def hours(story: fleet.Story) -> float | None:
    """Hours from the latest `Started:` to the latest `Pull request:`, or None without both."""
    started = log.last(story.issue, "Started:")
    opened = log.last(story.issue, "Pull request:")
    if started is None or opened is None:
        return None
    return (_stamp(opened.created_at) - _stamp(started.created_at)).total_seconds() / 3600


OVERLAPPING = 5  # finished stories that ran beside another, below which a median is noise


def _span(story: fleet.Story) -> tuple[datetime, datetime] | None:
    started = log.last(story.issue, "Started:")
    opened = log.last(story.issue, "Pull request:")
    if not story.closed or started is None or opened is None:
        return None
    return _stamp(started.created_at), _stamp(opened.created_at)


def concurrency(stories: list[fleet.Story]) -> int | None:
    """How many stories were in progress at once: the median count at every finished story's start.

    Measured from the log rather than from the sessions a machine can see, so a teammate's work
    counts; too few overlapping stories says nothing, and the caller falls back.
    """
    spans = [span for span in (_span(story) for story in stories) if span is not None]
    overlapped = sum(
        1 for i, (a, b) in enumerate(spans) if any(j != i and c < b and a < d for j, (c, d) in enumerate(spans))
    )
    if overlapped < OVERLAPPING:
        return None
    counts = [sum(1 for start, end in spans if start <= at < end) for at, _ in spans]
    return max(1, round(statistics.median(counts)))


def durations(stories: list[fleet.Story]) -> dict[int | None, list[float]]:
    """Hours from `Started:` to `Pull request:` for every closed story, banded by points."""
    found: dict[int | None, list[float]] = {}
    for story in stories:
        took = hours(story) if story.closed else None
        if took is not None:
            found.setdefault(story.points, []).append(took)
    return {points: sorted(found[points]) for points in sorted(found, key=lambda p: (p is None, p))}


def _placed(stories: list[fleet.Story], blockers: fleet.Blockers) -> list[fleet.Story]:
    return order.topological(stories, blockers, lambda story: (story.number, story.number, story.number))


def _waits(story: fleet.Story, blockers: fleet.Blockers, finish: dict[fleet.Key, float]) -> float:
    holds = blockers.get(story.key) or []
    return max((finish.get((where, number), 0.0) for where, number, _ in holds), default=0.0)


def floor(stories: list[fleet.Story], blockers: fleet.Blockers, sessions: int, hours: dict[int | None, float]) -> float:
    """`max(critical path, total work / sessions)`, both proven lower bounds on the makespan."""
    fallback = statistics.median(hours.values())
    duration = {story.key: hours.get(story.points, fallback) for story in stories}
    finish: dict[fleet.Key, float] = {}
    path = 0.0
    for story in _placed(stories, blockers):
        end = _waits(story, blockers, finish) + duration[story.key]
        finish[story.key] = end
        path = max(path, end)
    return max(path, sum(duration.values()) / max(sessions, 1))


def _schedule(
    placed: list[fleet.Story], blockers: fleet.Blockers, sessions: int, duration: dict[fleet.Key, float]
) -> float:
    """List scheduling: each story to whichever session frees up first, no earlier than its blockers finish."""
    free = [0.0] * max(sessions, 1)
    finish: dict[fleet.Key, float] = {}
    for story in placed:
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
    placed = _placed(stories, blockers)

    def _draw() -> dict[fleet.Key, float]:
        return {story.key: rng.choice(samples.get(story.points) or pool) for story in stories}

    makespans = sorted(_schedule(placed, blockers, sessions, _draw()) for _ in range(runs))
    return {p: _percentile(makespans, p) for p in PERCENTILES}


def _days(hours: float) -> int:
    return math.ceil(hours / HOURS_A_DAY)


def _row(label: str, hours: float, note: str) -> str:
    return f"  {label:<12}{_days(hours):>3} days   {note}"


def parallel(given: int | None, read: fleet.Fleet, live: int) -> tuple[int, str]:
    """How many stories the forecast runs at once, and where that number came from."""
    if given is not None:
        return given, "given"
    measured = concurrency(read.stories)
    if measured is not None:
        return measured, "measured"
    return max(live, 1), "open sessions"


def rows(read: fleet.Fleet, at_once: int, source: str) -> list[str]:
    """The floor, the commitment, and the control that pools points away, over what is not yet Done."""
    left = [story for story in read.stories if not story.closed]
    if not left:
        return ["  nothing left to forecast"]

    points = sum(story.points or 0 for story in left)
    story_word = "story" if len(left) == 1 else "stories"
    header = f"  {len(left)} {story_word}, {points} points, {at_once} at once, {source}"

    history = durations(read.stories)
    if not history:
        return [f"{header}: no finished stories, so no Floor and no Commitment."]

    pooled = sorted(hours for band in history.values() for hours in band)
    thin = any(len(band) < THIN for band in history.values())
    commitment_p = 100 if thin else 85
    worst_p = max(commitment_p, 95)  # never below the commitment, so a thin history cannot invert the two
    medians = {band: statistics.median(hours) for band, hours in history.items()}

    floor_hours = floor(left, read.blockers, at_once, medians)
    banded = simulate(left, read.blockers, at_once, history)
    unbanded = simulate(left, read.blockers, at_once, {None: pooled})
    commitment_note = ("worst run" if thin else "85th percentile") + ", banded by points"

    lines = [
        header,
        "",
        _row("Floor", floor_hours, "critical path, nothing stalls"),
        _row("Commitment", banded[commitment_p], commitment_note),
        _row("Worst seen", banded[worst_p], f"{worst_p}th percentile"),
        "",
        _row("Unbanded", unbanded[commitment_p], "the same, points ignored"),
        "",
        f"  From {len(pooled)} finished stories, the longest {pooled[-1]:.1f} hours against a median "
        f"of {statistics.median(pooled):.1f}.",
    ]
    if thin:
        lines.append(f"  Thin history: under {THIN} in a band, so the commitment is the worst run, not a fit.")
    return [*lines, "", f"  {POINT}"]
