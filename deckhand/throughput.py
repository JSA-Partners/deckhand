"""How many stories finish a week, and how many weeks what is left of an epic will take.

Nothing here reads GitHub or prints: it takes the fleet and today's date and returns numbers and the
lines of `epic forecast`. It is Monte Carlo simulation on measured weekly throughput, the method of
Daniel Vacanti's "When Will It Be Done?". A week's finished count already holds the client work, the
overhead stories, the weekends, the stalls and the sessions run side by side, so none is modeled.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from datetime import date, datetime

from deckhand import draft, fleet, log

# Floor, commitment, worst: with each run resampling its weeks, the 85th was met 82 to 86 percent of the time.
PERCENTILES = FLOOR, COMMITMENT, WORST = (50, 85, 99)
ROOTS = 5  # measured split trees below which a draw is noise, so the worst seen is used
PACED = 5  # an epic's finishes in its last LOOKBACK weeks below which its own weeks are noise
SETTLED = 4  # weeks since an epic's work began below which its pace has not settled
LOOKBACK = 12  # weeks of history drawn from, so a pace from long ago does not outvote this quarter's
HORIZON = 104  # weeks, two years, at which a commitment says nothing a stakeholder can plan on

THIN = "too little history"
FEW = "too few finished"
IDLE = "nothing finished in the weeks measured"
FAR = "over two years"
FLAT = "too little variation"


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


def _sizes(measured: list[int]) -> tuple[str, list[int]]:
    """How the split size was obtained, "measured", "largest" or "assumed", and the sizes to draw from."""
    if len(measured) >= ROOTS:
        return "measured", measured
    if measured:
        return "largest", [max(measured)]
    return "assumed", [1]


def _day(stamp: str) -> date:
    """The local calendar day of a GitHub timestamp, the calendar `date.today()` reads."""
    return datetime.fromisoformat(stamp).astimezone().date()


def _held(stories: list[fleet.Story]) -> list[fleet.Story]:
    return [story for story in stories if fleet.touched(story) and not story.dropped]


def _finished_on(stories: list[fleet.Story], today: date) -> list[date]:
    found = (_day(story.closed_at) for story in _held(stories) if story.closed and story.closed_at)
    return [day for day in found if day <= today]


def weekly(stories: list[fleet.Story], today: date, start: date | None = None) -> list[int]:
    """Finished stories per seven days, oldest first, from the week of `start` or the earliest finish to today's."""
    done = _finished_on(stories, today)
    first = min(done, default=None) if start is None else start
    if first is None or first > today:
        return []
    counts = [0] * ((today - first).days // 7 + 1)
    for day in done:
        if day >= first:
            counts[(today - day).days // 7] += 1
    return counts[::-1]


def began(stories: list[fleet.Story], today: date) -> date | None:
    """The day work on `stories` began: the earliest `Started:` entry or finish among them, whichever came first."""
    started = [
        _day(entry.created_at)
        for story in _held(stories)
        for entry in log.entries(story.issue)
        if entry.prefix == "Started:" and entry.created_at
    ]
    return min([*started, *_finished_on(stories, today)], default=None)


def pace(every: list[fleet.Story], epic: fleet.Key, today: date) -> tuple[list[int], bool]:
    """The epic's own last `LOOKBACK` full weekly counts since its work began, and whether `SETTLED` weeks have passed.

    Only the epic's stories count: the project's weeks hold work outside the epic and forecast it early.
    The oldest week is left out when work began partway through it, since it would count a few days as seven.
    """
    mine = fleet.members(every, epic)
    start = began(mine, today)
    if start is None:
        return [], False
    days = (today - start).days
    counts = weekly(mine, today, start)
    full = counts if days % 7 == 6 else counts[1:]
    return full[-LOOKBACK:], days >= 7 * SETTLED


def remaining(read: fleet.Fleet, epic: fleet.Key) -> tuple[int, int]:
    """The epic's open stories counted as one each, and apart from them its drafts with no stories listed yet."""
    left = [
        story
        for story in fleet.members([*read.stories, *read.archived], epic)
        if not story.closed and not story.dropped
    ]
    unsplit = sum(1 for story in left if draft.is_draft(story.issue.body) and not draft.read(story.issue.body)[1])
    return len(left) - unsplit, unsplit


def _percentile(sorted_values: list[int], p: int) -> int:
    index = math.ceil(p / 100 * len(sorted_values)) - 1
    return sorted_values[min(len(sorted_values) - 1, max(0, index))]


def simulate(
    items: int, unsplit: int, samples: list[int], sizes: list[int], runs: int = 10_000, seed: int = 0
) -> dict[int, int | None] | None:
    """The weeks to finish at `PERCENTILES`, each run resampling `samples` and drawing weeks from that until done.

    Resampling per run carries the doubt about the pace itself, so a short history gives a wide range.
    A percentile that reaches `HORIZON` is None, since no run measured it; the whole forecast is None
    when nothing is left, no week in `samples` finished anything, or the commitment reaches `HORIZON`.
    """
    if items + unsplit == 0 or not any(samples):
        return None
    rng = random.Random(seed)
    taken = []
    for _ in range(runs):
        left = items + sum(rng.choice(sizes) for _ in range(unsplit))
        run_samples = [rng.choice(samples) for _ in samples]
        weeks = 0
        while left > 0 and weeks < HORIZON:
            left -= rng.choice(run_samples)
            weeks += 1
        taken.append(weeks)
    taken.sort()
    found = {p: _percentile(taken, p) for p in PERCENTILES}
    if found[COMMITMENT] >= HORIZON:
        return None
    return {p: None if weeks >= HORIZON else weeks for p, weeks in found.items()}


@dataclass(frozen=True)
class Outlook:
    """One epic's forecast in weeks and the history it was drawn from; without one, None weeks and a `reason`."""

    left: int
    floor_weeks: int | None
    commitment_weeks: int | None
    worst_weeks: int | None
    pace: str | None
    weeks: int
    per_week: float
    unsplit: int
    split_size: float
    split_basis: str | None
    measured: int
    reason: str | None


def _short(samples: list[int], settled: bool) -> str | None:
    """Why the epic's own weeks cannot pace a forecast yet, or None when they can."""
    if not settled:
        return THIN
    if not any(samples):
        return IDLE
    if sum(samples) < PACED:
        return FEW
    return FLAT if len(set(samples)) == 1 else None


def outlook(read: fleet.Fleet, epic: fleet.Key, today: date, seed: int = 0) -> Outlook:
    """The forecast over what is left of `epic`, drawn from its own stories, archived ones too."""
    every = [*read.stories, *read.archived]
    items, unsplit = remaining(read, epic)
    samples, settled = pace(every, epic, today)
    measured = splits(every)
    basis, sizes = _sizes(measured)
    short = _short(samples, settled)
    found = None if short else simulate(items, unsplit, samples, sizes, seed=seed)
    reason = None if found is not None or not items + unsplit else short or FAR
    return Outlook(
        left=items + unsplit,
        floor_weeks=None if found is None else found[FLOOR],
        commitment_weeks=None if found is None else found[COMMITMENT],
        worst_weeks=None if found is None else found[WORST],
        pace=None if found is None else "epic",
        weeks=len(samples),
        per_week=round(statistics.fmean(samples), 2) if samples else 0.0,
        unsplit=unsplit,
        split_size=statistics.fmean(sizes) if unsplit else 1.0,
        split_basis=basis if unsplit else None,
        measured=len(measured),
        reason=reason,
    )


_WHY = {
    THIN: f"Work on this feature began fewer than {SETTLED} weeks ago, so there is no date range yet.",
    FEW: f"Fewer than {PACED} of this feature's stories have finished in the weeks measured, "
    "so there is no date range yet.",
    IDLE: "No story of this feature finished in the weeks measured, so there is no date range.",
    FAR: "The measured pace would take over two years, so there is no date range.",
    FLAT: "This feature's weekly pace has not varied yet, so a date range would be falsely precise.",
}


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _row(label: str, weeks: int, note: str) -> str:
    unit = "week" if weeks == 1 else "weeks"
    return f"  {label:<12}{weeks:>3} {unit:<5}   {note}"


def rows(found: Outlook) -> list[str]:
    """The floor, the commitment and the worst case in weeks, and what the pace was measured from."""
    if found.left == 0:
        return ["  nothing left to forecast"]
    header = f"  {_plural(found.left, 'piece')} left"
    if found.floor_weeks is None or found.commitment_weeks is None:
        return [header, "", f"  {_WHY[found.reason or IDLE]}"]
    worst = (
        f"  {'Worst case':<12}beyond two years"
        if found.worst_weeks is None
        else _row("Worst case", found.worst_weeks, f"{WORST}th percentile")
    )
    lines = [
        header,
        "",
        _row("Floor", found.floor_weeks, f"{FLOOR}th percentile"),
        _row("Commitment", found.commitment_weeks, f"{COMMITMENT}th percentile"),
        worst,
        "",
        f"  Paced by this feature's own {_plural(found.weeks, 'week')}, "
        f"an average of {found.per_week:.1f} stories a week.",
    ]
    if found.unsplit:
        lines.append(_drafts(found))
    return lines


def _drafts(found: Outlook) -> str:
    drafts = f"  {_plural(found.unsplit, 'draft')} not yet split, {'each ' if found.unsplit > 1 else ''}counted as"
    parked = _plural(found.measured, "finished parked feature")
    if found.split_basis == "measured":
        return f"{drafts} {found.split_size:.1f} stories, the average of {parked}."
    if found.split_basis == "largest":
        size = int(found.split_size)
        return f"{drafts} {size} {'story' if size == 1 else 'stories'}, the largest of {parked}."
    return f"{drafts} 1 story, since no parked feature has finished yet."
