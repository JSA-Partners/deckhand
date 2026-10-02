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
from datetime import datetime

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


ROOTS = 5  # measured split trees below which a draw is noise, so the worst seen is used


def _was_parked(story: fleet.Story) -> bool:
    return any(e.prefix == "Drafted:" and e.text.startswith("parked from") for e in log.entries(story.issue))


def splits(stories: list[fleet.Story]) -> list[int]:
    """How many stories each finished parked feature turned out to be: the size of its split tree, ascending.

    A root is a finished story that began as a parked feature and that no other story shed. Its tree
    is itself and everything its `Split:` entries name, however deep, counted among the stories given.
    A story closed as not planned is no piece of what a feature became, so it is neither.
    """
    held = {story.key: story for story in stories if not story.dropped}
    kids = {key: [kid for kid in fleet.shed(story) if kid in held] for key, story in held.items()}
    was_shed = {key for found in kids.values() for key in found}

    def _size(key: fleet.Key, seen: set[fleet.Key]) -> int:
        if key in seen:
            return 0
        seen.add(key)
        return 1 + sum(_size(kid, seen) for kid in kids[key])

    return sorted(
        _size(story.key, set())
        for story in held.values()
        if story.closed and fleet.touched(story) and story.key not in was_shed and _was_parked(story)
    )


def _sizes(measured: list[int]) -> list[int]:
    return measured if len(measured) >= ROOTS else [max(measured, default=1)]


def refinements(stories: list[fleet.Story]) -> list[float]:
    """Hours from a story being written, its latest `Drafted:`, to its latest `Review:`, for every finished one.

    The time to write a draft into a story has no log entry and is not counted.
    """
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


UTILIZATION_FLOOR = 0.01  # a history that is nearly all gaps would stretch a forecast without limit
NEGLIGIBLE = 0.005  # an idle share below this prints as 0%, so the rows claim no stretch for it


def utilization(stories: list[fleet.Story], at_once: int) -> float:
    """The share of session hours, first start to last pull request, that finished stories filled."""
    spans = [span for span in (_span(story) for story in stories) if span is not None]
    if len(spans) < 2:
        return 1.0
    window = (max(end for _, end in spans) - min(start for start, _ in spans)).total_seconds()
    if window <= 0:
        return 1.0
    worked = sum((end - start).total_seconds() for start, end in spans)
    return min(1.0, max(UTILIZATION_FLOOR, worked / (max(at_once, 1) * window)))


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
    grown: frozenset[fleet.Key] = frozenset(),
    size: float = 1.0,
    review: float = 0.0,
    pooled: float | None = None,
) -> float:
    """`max(critical path, total work / sessions)`, both proven lower bounds on the makespan."""
    fallback = statistics.median(hours.values())

    def _took(story: fleet.Story) -> float:
        if story.key not in drafts:
            return hours.get(story.points, fallback)
        count = size if story.key in grown else 1.0
        return ((fallback if pooled is None else pooled) + review) * count

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
    grown: frozenset[fleet.Key] = frozenset(),
    sizes: list[int] | None = None,
    reviews: list[float] | None = None,
) -> dict[int, float]:
    """The makespan at `PERCENTILES`, from `runs` schedules drawn from `samples`.

    A draft is unsized, so each story it becomes draws from every finished duration and adds a review.
    """
    rng = random.Random(seed)
    pool = [value for band in samples.values() for value in band]
    placed = _placed(stories, blockers)

    def _draft(story: fleet.Story) -> float:
        count = rng.choice(sizes) if story.key in grown and sizes else 1
        return sum(rng.choice(pool) + (rng.choice(reviews) if reviews else 0.0) for _ in range(count))

    def _draw() -> dict[fleet.Key, float]:
        return {
            story.key: _draft(story) if story.key in drafts else rng.choice(samples.get(story.points) or pool)
            for story in stories
        }

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
    pace: str = "project"


def remaining(read: fleet.Fleet, scope: fleet.Key | None = None) -> list[fleet.Story]:
    """Every open story the process owns, inside the epic `scope` names when one is given."""
    return [
        story
        for story in read.stories
        if not story.closed and fleet.touched(story) and (scope is None or story.parent == scope)
    ]


PACED = 5  # an epic's finished stories below which its own pace is noise, so the project's is used


def _paced(every: list[fleet.Story], scope: fleet.Key) -> list[fleet.Story]:
    return [story for story in fleet.members(every, scope) if not story.dropped and _span(story) is not None]


def outlook(read: fleet.Fleet, at_once: int, scope: fleet.Key | None = None, seed: int | None = None) -> Outlook | None:
    """The forecast over what is left in `scope`, or None when nothing is left or nothing has finished.

    The durations are every finished story the project holds, archived ones too, whatever the scope:
    an epic's own stories are too few to measure from, and the board archives finished work. The
    pace is the epic's own once it has `PACED` finished stories, because the work outside any epic
    competes for the same hours and the project's pace would forecast the epic too early.
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
    grown = frozenset(story.key for story in left if story.key in drafts and not draft.read(story.issue.body)[1])
    drawn, reviews = _sizes(splits(every)), refinements(every)
    review = statistics.median(reviews) if reviews else 0.0
    mine = [] if scope is None else _paced(every, scope)
    paced = len(mine) >= PACED
    used = utilization(mine if paced else every, at_once)

    grow = {"drafts": drafts, "grown": grown, "sizes": drawn, "reviews": reviews}
    banded = simulate(left, read.blockers, at_once, history, seed=seed, **grow)
    unbanded = simulate(left, read.blockers, at_once, {None: pooled}, seed=seed, **grow)
    middle = statistics.median(pooled)
    least = floor(left, read.blockers, at_once, medians, drafts, grown, statistics.median(drawn), review, middle)
    return Outlook(
        left=len(left),
        points=sum(story.points or 0 for story in left),
        samples=len(pooled),
        longest=pooled[-1],
        median=middle,
        thin=thin,
        floor=least / used,
        commitment=banded[commitment_p] / used,
        worst=banded[worst_p] / used,
        unbanded=unbanded[commitment_p] / used,
        drafts=len(drafts),
        growth=statistics.fmean(drawn),
        idle=1 - used,
        pace="epic" if paced else "project",
    )


def rows(
    read: fleet.Fleet, at_once: int, source: str, scope: fleet.Key | None = None, seed: int | None = None
) -> list[str]:
    """The floor, the commitment, and the control that pools points away, over what is left in `scope`."""
    left = remaining(read, scope)
    if not left:
        return ["  nothing left to forecast"]

    points = sum(story.points or 0 for story in left)
    story_word = "story" if len(left) == 1 else "stories"
    header = f"  {len(left)} {story_word}, {points} points, {at_once} at once, {source}"

    found = outlook(read, at_once, scope, seed)
    if found is None:
        return [f"{header}: no finished stories, so no Floor and no Commitment."]

    commitment_note = ("worst run" if found.thin else "85th percentile") + ", banded by points"
    worst_p = 100 if found.thin else 95
    stretched = found.idle >= NEGLIGIBLE
    floor_note = "critical path at the measured pace" if stretched else "critical path, nothing stalls"
    lines = [
        header,
        "",
        _row("Floor", found.floor, floor_note),
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
        counted = f"counted as {found.growth:.1f} stories of any size on average, each with a review."
        lines.append(f"  {found.drafts} {draft_word} {counted}")
    if scope is not None and found.pace == "epic":
        count = len(_paced([*read.stories, *read.archived], scope))
        lines.append(f"  Paced by this epic's own {count} finished stories.")
    elif scope is not None:
        lines.append(f"  Paced by the whole project: this epic has under {PACED} finished stories.")
    if stretched:
        lines.append(f"  Stretched for the {found.idle:.0%} of the time nothing was in progress.")
    if found.thin:
        lines.append(f"  Thin history: under {THIN} in a band, so the commitment is the worst run, not a fit.")
    return [*lines, "", f"  {POINT}"]
