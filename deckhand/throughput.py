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
PACED = 5  # an epic's finishes in its last LOOKBACK weeks below which its own weeks are noise
SETTLED = 4  # weeks since the first finish counted below which a pace has not settled
LOOKBACK = 12  # weeks of history drawn from, so a pace from long ago does not outvote this quarter's
HORIZON = 104  # weeks, two years, at which a commitment says nothing a stakeholder can plan on

THIN = "too little history"
IDLE = "nothing finished in the weeks measured"
FAR = "over two years"


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


def _settled(stories: list[fleet.Story], today: date) -> bool:
    done = _finished_on(stories)
    return bool(done) and (today - min(done)).days >= 7 * SETTLED


def pace(every: list[fleet.Story], epic: fleet.Key, today: date) -> tuple[str, list[int], bool]:
    """Which history paces the epic, "epic" or "project", its last `LOOKBACK` weekly counts, and whether it settled.

    The epic's own once it has `PACED` finished stories in those weeks and `SETTLED` weeks since its
    first, because the project's count holds work outside the epic and would forecast it early; the
    project's before that. Either pace is settled only `SETTLED` weeks after the first finish it counts.
    """
    mine = fleet.members(every, epic)
    own = weekly(mine, today)[-LOOKBACK:]
    if sum(own) >= PACED and _settled(mine, today):
        return "epic", own, True
    return "project", weekly(every, today)[-LOOKBACK:], _settled(every, today)


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

    None when nothing is left, no week in `samples` finished anything, or the commitment reaches `HORIZON`.
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
    found = {p: _percentile(taken, p) for p in PERCENTILES}
    return None if found[85] >= HORIZON else found


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


def outlook(read: fleet.Fleet, epic: fleet.Key, today: date, seed: int = 0) -> Outlook:
    """The forecast over what is left of `epic`, drawn from every finished story, archived ones too."""
    every = [*read.stories, *read.archived]
    items, unsplit = remaining(read, epic)
    paced, samples, settled = pace(every, epic, today)
    measured = splits(every)
    basis, sizes = _sizes(measured)
    found = simulate(items, unsplit, samples, sizes, seed=seed) if settled else None
    reason = None
    if found is None and items + unsplit:
        reason = THIN if not settled else IDLE if not any(samples) else FAR
    return Outlook(
        left=items + unsplit,
        floor_weeks=None if found is None else found[50],
        commitment_weeks=None if found is None else found[85],
        worst_weeks=None if found is None else found[95],
        pace=None if found is None else paced,
        weeks=len(samples),
        per_week=round(statistics.fmean(samples), 2) if samples else 0.0,
        unsplit=unsplit,
        split_size=statistics.fmean(sizes) if unsplit else 1.0,
        split_basis=basis if unsplit else None,
        measured=len(measured),
        reason=reason,
    )


_WHY = {
    THIN: f"Fewer than {SETTLED} weeks of finished stories, so there is no date range yet.",
    IDLE: f"No story finished in the last {LOOKBACK} weeks, so there is no date range.",
    FAR: "The measured pace would take over two years, so there is no date range.",
}


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
        return [header, "", f"  {_WHY[found.reason or IDLE]}"]
    lines = [
        header,
        "",
        _row("Floor", found.floor_weeks, "50th percentile"),
        _row("Commitment", found.commitment_weeks, "85th percentile"),
        _row("Worst seen", found.worst_weeks, "95th percentile"),
        "",
    ]
    rate = f"an average of {found.per_week:.1f} stories a week."
    if found.pace == "epic":
        lines.append(f"  Paced by this epic's own {_plural(found.weeks, 'week')}, {rate}")
    else:
        lines.append(f"  Paced by the whole project's last {_plural(found.weeks, 'week')}, {rate}")
        lines.append("  That includes work outside this epic, so it leans early.")
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
