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
from dataclasses import dataclass
from datetime import datetime, timedelta

from deckhand import draft, fleet, log, order

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


ROOTS = 5  # finished pieces of work below which their mean size is noise, so nothing is grown


def growth(stories: list[fleet.Story]) -> float:
    """How many stories one finished piece of work turned out to be: the mean size of a split tree.

    A root is a finished story no other story shed. Its tree is itself and everything its `Split:`
    entries name, however deep, counted among the stories given.
    """
    held = {story.key: story for story in stories}
    kids = {story.key: [key for key in fleet.shed(story) if key in held] for story in stories}
    was_shed = {key for found in kids.values() for key in found}

    def _size(key: fleet.Key, seen: set[fleet.Key]) -> int:
        if key in seen:
            return 0
        seen.add(key)
        return 1 + sum(_size(kid, seen) for kid in kids[key])

    sizes = [
        _size(story.key, set())
        for story in stories
        if story.closed and fleet.touched(story) and story.key not in was_shed
    ]
    return sum(sizes) / len(sizes) if len(sizes) >= ROOTS else 1.0


def refinements(stories: list[fleet.Story]) -> list[float]:
    """Hours from the latest `Drafted:` to the latest `Review:` for every finished story with both."""
    found = []
    for story in stories:
        drafted = log.last(story.issue, "Drafted:")
        reviewed = log.last(story.issue, "Review:")
        if not story.closed or drafted is None or reviewed is None:
            continue
        took = (_stamp(reviewed.created_at) - _stamp(drafted.created_at)).total_seconds() / 3600
        if took > 0:
            found.append(took)
    return sorted(found)


IDLE_CAP = 0.9  # a history that is nearly all gaps would stretch a forecast without limit


def idle(stories: list[fleet.Story]) -> float:
    """The share of days, first start to last pull request, on which no finished story was in progress."""
    spans = [span for span in (_span(story) for story in stories) if span is not None]
    if len(spans) < THIN:
        return 0.0
    first = min(start for start, _ in spans).date()
    last = max(end for _, end in spans).date()
    days = (last - first).days + 1
    busy = {
        start.date() + timedelta(days=offset)
        for start, end in spans
        for offset in range((end.date() - start.date()).days + 1)
    }
    return min(IDLE_CAP, (days - len(busy)) / days)


def _placed(stories: list[fleet.Story], blockers: fleet.Blockers) -> list[fleet.Story]:
    return order.topological(stories, blockers, lambda story: (story.number, story.number, story.number))


def _waits(story: fleet.Story, blockers: fleet.Blockers, finish: dict[fleet.Key, float]) -> float:
    holds = blockers.get(story.key) or []
    return max((finish.get((where, number), 0.0) for where, number, _ in holds), default=0.0)


def floor(
    stories: list[fleet.Story],
    blockers: fleet.Blockers,
    sessions: int,
    hours: dict[int | None, float],
    drafts: frozenset[fleet.Key] = frozenset(),
    growth: float = 1.0,
    review: float = 0.0,
) -> float:
    """`max(critical path, total work / sessions)`, both proven lower bounds on the makespan."""
    fallback = statistics.median(hours.values())

    def _took(story: fleet.Story) -> float:
        took = hours.get(story.points, fallback)
        return took * growth + review if story.key in drafts else took

    duration = {story.key: _took(story) for story in stories}
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
    drafts: frozenset[fleet.Key] = frozenset(),
    growth: float = 1.0,
    reviews: list[float] | None = None,
) -> dict[int, float]:
    """The makespan at `PERCENTILES`, from `runs` schedules drawn from `samples`."""
    rng = random.Random(seed)
    pool = [value for band in samples.values() for value in band]
    placed = _placed(stories, blockers)

    def _draw() -> dict[fleet.Key, float]:
        drawn: dict[fleet.Key, float] = {}
        for story in stories:
            took = rng.choice(samples.get(story.points) or pool)
            if story.key in drafts:
                took = took * growth + (rng.choice(reviews) if reviews else 0.0)
            drawn[story.key] = took
        return drawn

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
    measured = concurrency([*read.stories, *read.archived])
    if measured is not None:
        return measured, "measured"
    return max(live, 1), "open sessions"


@dataclass(frozen=True)
class Outlook:
    """One forecast in hours: what is left, the history it draws from, and each line's number."""

    left: int
    points: int
    samples: int
    longest: float
    median: float
    thin: bool
    floor: float
    commitment: float
    worst: float
    unbanded: float
    drafts: int = 0
    growth: float = 1.0
    idle: float = 0.0


def remaining(read: fleet.Fleet, scope: fleet.Key | None = None) -> list[fleet.Story]:
    """Every open story the process owns, inside the epic `scope` names when one is given."""
    return [
        story
        for story in read.stories
        if not story.closed and fleet.touched(story) and (scope is None or story.parent == scope)
    ]


def outlook(read: fleet.Fleet, at_once: int, scope: fleet.Key | None = None, seed: int | None = None) -> Outlook | None:
    """The forecast over what is left in `scope`, or None when nothing is left or nothing has finished.

    The history is every finished story the project holds, archived ones too, whatever the scope:
    an epic's own stories are too few to measure from, and the board archives finished work.
    """
    left = remaining(read, scope)
    every = [*read.stories, *read.archived]
    history = durations(every)
    if not left or not history:
        return None

    pooled = sorted(hours for band in history.values() for hours in band)
    thin = any(len(band) < THIN for band in history.values())
    commitment_p = 100 if thin else 85
    worst_p = max(commitment_p, 95)  # never below the commitment, so a thin history cannot invert the two
    medians = {band: statistics.median(hours) for band, hours in history.items()}

    drafts = frozenset(story.key for story in left if draft.is_draft(story.issue.body))
    grown, reviews = growth(every), refinements(every)
    review = statistics.median(reviews) if reviews else 0.0
    gap = idle(every)

    banded = simulate(left, read.blockers, at_once, history, seed=seed, drafts=drafts, growth=grown, reviews=reviews)
    unbanded = simulate(
        left, read.blockers, at_once, {None: pooled}, seed=seed, drafts=drafts, growth=grown, reviews=reviews
    )
    return Outlook(
        left=len(left),
        points=sum(story.points or 0 for story in left),
        samples=len(pooled),
        longest=pooled[-1],
        median=statistics.median(pooled),
        thin=thin,
        floor=floor(left, read.blockers, at_once, medians, drafts, grown, review) / (1 - gap),
        commitment=banded[commitment_p] / (1 - gap),
        worst=banded[worst_p] / (1 - gap),
        unbanded=unbanded[commitment_p] / (1 - gap),
        drafts=len(drafts),
        growth=grown,
        idle=gap,
    )


def rows(read: fleet.Fleet, at_once: int, source: str, scope: fleet.Key | None = None) -> list[str]:
    """The floor, the commitment, and the control that pools points away, over what is left in `scope`."""
    left = remaining(read, scope)
    if not left:
        return ["  nothing left to forecast"]

    points = sum(story.points or 0 for story in left)
    story_word = "story" if len(left) == 1 else "stories"
    header = f"  {len(left)} {story_word}, {points} points, {at_once} at once, {source}"

    found = outlook(read, at_once, scope)
    if found is None:
        return [f"{header}: no finished stories, so no Floor and no Commitment."]

    commitment_note = ("worst run" if found.thin else "85th percentile") + ", banded by points"
    worst_p = 100 if found.thin else 95
    lines = [
        header,
        "",
        _row("Floor", found.floor, "critical path, nothing stalls"),
        _row("Commitment", found.commitment, commitment_note),
        _row("Worst seen", found.worst, f"{worst_p}th percentile"),
        "",
        _row("Unbanded", found.unbanded, "the same, points ignored"),
        "",
        f"  From {found.samples} finished stories, the longest {found.longest:.1f} hours against a median "
        f"of {found.median:.1f}.",
    ]
    if found.drafts:
        draft_word = "draft" if found.drafts == 1 else "drafts"
        lines.append(f"  {found.drafts} {draft_word} counted as {found.growth:.1f} stories of any size, plus a review.")
    if found.idle:
        lines.append(f"  Stretched for the {found.idle:.0%} of days on which nothing was in progress.")
    if found.thin:
        lines.append(f"  Thin history: under {THIN} in a band, so the commitment is the worst run, not a fit.")
    return [*lines, "", f"  {POINT}"]
