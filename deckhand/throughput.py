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

PERCENTILES = (50, 85, 95)
ROOTS = 5  # measured split trees below which a draw is noise, so the worst seen is used
PACED = 5  # an epic's finished stories below which its own weeks are noise, so the project's are used
SETTLED = 4  # weeks since an epic's first finish below which its own pace has not settled
LOOKBACK = 12  # weeks of the project's history, so a pace from long ago does not outvote this quarter's
HORIZON = 520  # weeks, ten years, after which a run that has not finished stops counting


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


def _finished_on(stories: list[fleet.Story]) -> list[date]:
    return [
        datetime.fromisoformat(story.closed_at).date()
        for story in stories
        if story.closed and story.closed_at and fleet.touched(story) and not story.dropped
    ]


def weekly(stories: list[fleet.Story], today: date) -> list[int]:
    """Finished stories per seven days, oldest first, from the week of the earliest finish to the week ending today."""
    back = [max(0, (today - day).days // 7) for day in _finished_on(stories)]
    if not back:
        return []
    counts = [0] * (max(back) + 1)
    for weeks in back:
        counts[weeks] += 1
    return counts[::-1]


def pace(every: list[fleet.Story], epic: fleet.Key, today: date) -> tuple[str, list[int]]:
    """Which history paces the epic, "epic" or "project", and its weekly counts.

    The epic's own once it has `PACED` finished stories over `SETTLED` weeks, because the project's
    count holds work outside the epic and would forecast it early; the project's last `LOOKBACK`
    weeks before that.
    """
    mine = fleet.members(every, epic)
    done = _finished_on(mine)
    if len(done) >= PACED and (today - min(done)).days >= 7 * SETTLED:
        return "epic", weekly(mine, today)
    return "project", weekly(every, today)[-LOOKBACK:]


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
) -> dict[int, int] | None:
    """The weeks to finish at `PERCENTILES`, each run drawing a week's count from `samples` until nothing is left.

    None when nothing is left or no week in `samples` finished anything.
    """
    if items + unsplit == 0 or not any(samples):
        return None
    rng = random.Random(seed)
    taken = []
    for _ in range(runs):
        left = items + sum(rng.choice(sizes) for _ in range(unsplit))
        weeks = 0
        while left > 0 and weeks < HORIZON:
            left -= rng.choice(samples)
            weeks += 1
        taken.append(weeks)
    taken.sort()
    return {p: _percentile(taken, p) for p in PERCENTILES}


@dataclass(frozen=True)
class Outlook:
    """One epic's forecast in weeks, and the history it was drawn from; the weeks are None without one."""

    left: int
    floor_weeks: int | None
    commitment_weeks: int | None
    worst_weeks: int | None
    pace: str | None
    weeks: int
    per_week: float
    unsplit: int
    split_size: float


def outlook(read: fleet.Fleet, epic: fleet.Key, today: date, seed: int = 0) -> Outlook:
    """The forecast over what is left of `epic`, drawn from every finished story, archived ones too."""
    every = [*read.stories, *read.archived]
    items, unsplit = remaining(read, epic)
    paced, samples = pace(every, epic, today)
    sizes = _sizes(splits(every))
    found = simulate(items, unsplit, samples, sizes, seed=seed)
    return Outlook(
        left=items + unsplit,
        floor_weeks=None if found is None else found[50],
        commitment_weeks=None if found is None else found[85],
        worst_weeks=None if found is None else found[95],
        pace=None if found is None else paced,
        weeks=len(samples),
        per_week=float(statistics.median(samples)) if samples else 0.0,
        unsplit=unsplit,
        split_size=statistics.fmean(sizes) if unsplit else 1.0,
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _row(label: str, weeks: int, note: str) -> str:
    unit = "week" if weeks == 1 else "weeks"
    return f"  {label:<12}{weeks:>3} {unit:<5}   {note}"


def rows(found: Outlook) -> list[str]:
    """The floor, the commitment and the worst seen in weeks, and what the pace was measured from."""
    if found.left == 0:
        return ["  nothing left to forecast"]
    header = f"  {_plural(found.left, 'piece')} left"
    if found.floor_weeks is None or found.commitment_weeks is None or found.worst_weeks is None:
        return [f"{header}, and no story finished in the weeks measured, so no forecast."]
    lines = [
        header,
        "",
        _row("Floor", found.floor_weeks, "50th percentile"),
        _row("Commitment", found.commitment_weeks, "85th percentile"),
        _row("Worst seen", found.worst_weeks, "95th percentile"),
        "",
    ]
    rate = f"a median of {found.per_week:.1f} stories a week."
    if found.pace == "epic":
        lines.append(f"  Paced by this epic's own {_plural(found.weeks, 'week')}, {rate}")
    else:
        lines.append(f"  Paced by the whole project's last {_plural(found.weeks, 'week')}, {rate}")
        lines.append("  That includes work outside this epic, so it leans early.")
    if found.unsplit:
        drafts = _plural(found.unsplit, "unsplit draft")
        lines.append(
            f"  {drafts} counted as {found.split_size:.1f} stories each, the average a finished parked feature became."
        )
    return lines
