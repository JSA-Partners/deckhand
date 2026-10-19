"""How many stories finish a week, and how many weeks what is left of a feature or the board will take.

Nothing here reads GitHub or prints: it takes the fleet and today's date and returns numbers and the
lines of `epic forecast`. It is Monte Carlo simulation on measured weekly throughput, the method of
Daniel Vacanti's "When Will It Be Done?". A week the whole board stalled is drawn at the board's own
rate of stalls, so a client week or a planning week in a short history does not pull every week down.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from deckhand import draft, fleet, log

# Likely is the 85th percentile, the line ActionableAgile draws; early is the median.
PERCENTILES = EARLY, LIKELY, WORST = (50, 85, 99)
# Below this many finished split trees a draw is noise, so the largest is used.
ROOTS = 5
# Below this many finishes in the weeks measured, a feature's own weeks are noise.
PACED = 5
# Below this many weeks since a feature's work began, its pace has not settled.
SETTLED = 4
# A board week under this share of its median week is a stall: client work or planning, not a slow week.
STALL = 0.5
# Weeks of history drawn from, so a pace from long ago does not outvote this quarter's.
LOOKBACK = 12
# Two years in weeks, past which a likely date says nothing a stakeholder can plan on.
HORIZON = 104
RESIDUE = 1e-9

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
    held = {fleet.folded(story.key): story for story in stories if not story.dropped}
    kids = {key: [kid for kid in fleet.shed(story) if kid in held] for key, story in held.items()}
    was_shed = {key for found in kids.values() for key in found}

    def _size(key: fleet.Key, seen: set[fleet.Key]) -> int:
        if key in seen:
            return 0
        seen.add(key)
        return 1 + sum(_size(kid, seen) for kid in kids[key])

    return sorted(
        _size(fleet.folded(story.key), set())
        for story in held.values()
        if story.closed and fleet.touched(story) and fleet.folded(story.key) not in was_shed and _was_parked(story)
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


def _paced(stories: list[fleet.Story], today: date) -> tuple[list[int], bool]:
    """The last `LOOKBACK` full weekly counts since work on `stories` began, and whether `SETTLED` weeks passed.

    The oldest week is left out when work began partway through it, since it would count a few days as seven.
    """
    start = began(stories, today)
    if start is None:
        return [], False
    days = (today - start).days
    counts = weekly(stories, today, start)
    full = counts if days % 7 == 6 else counts[1:]
    return full[-LOOKBACK:], days >= 7 * SETTLED


def pace(every: list[fleet.Story], feature: str, today: date) -> tuple[list[int], bool]:
    """The feature's own weekly counts: the project's weeks hold work outside it and would forecast it early."""
    return _paced(fleet.members(every, feature), today)


def stalls(counts: list[int]) -> list[bool]:
    """Which of the board's weekly counts are stalls: under `STALL` of its median week."""
    if not counts:
        return []
    line = statistics.median(counts) * STALL
    return [count < line for count in counts]


def _split(samples: list[int], flags: list[bool]) -> tuple[list[int], list[int]]:
    """The weeks of `samples` the board worked and the weeks it stalled; both end on today's week."""
    verdicts = flags[-len(samples) :] if samples else []
    working = [count for count, stalled in zip(samples, verdicts, strict=True) if not stalled]
    stalled = [count for count, stalled in zip(samples, verdicts, strict=True) if stalled]
    return working, stalled


def _share(samples: list[int], flags: list[bool], board: list[int]) -> float | None:
    """The feature's finishes over the board's in the weeks the board worked, or None with no such week."""
    verdicts = flags[-len(samples) :] if samples else []
    weeks = [index for index, stalled in enumerate(verdicts) if not stalled]
    whole = sum(board[-len(samples) :][index] for index in weeks)
    return sum(samples[index] for index in weeks) / whole if whole else None


def _left(stories: list[fleet.Story]) -> tuple[int, int]:
    left = [story for story in stories if not story.closed]
    unsplit = sum(1 for story in left if draft.is_draft(story.issue.body) and not draft.read(story.issue.body)[1])
    return len(left) - unsplit, unsplit


def remaining(read: fleet.Fleet, feature: str) -> tuple[int, int]:
    """The feature's open stories counted as one each, and apart from them its drafts with no stories listed yet."""
    return _left(fleet.members([*read.stories, *read.archived], feature))


def _percentile(sorted_values: list[int], p: int) -> int:
    index = math.ceil(p / 100 * len(sorted_values)) - 1
    return sorted_values[min(len(sorted_values) - 1, max(0, index))]


def _resample(rng: random.Random, values: list, kept: Callable[[list], bool]) -> list:
    """`values` drawn again to their own length until `kept` accepts the draw."""
    drawn = [rng.choice(values) for _ in values]
    while not kept(drawn):
        drawn = [rng.choice(values) for _ in values]
    return drawn


def simulate(
    items: int,
    unsplit: int,
    samples: list[int],
    sizes: list[int],
    flags: list[bool] | None = None,
    stalled: list[int] | None = None,
    runs: int = 10_000,
    seed: int = 0,
) -> dict[int, int | None] | None:
    """The weeks to finish at `PERCENTILES`, each run resampling `samples` and drawing weeks from that until done.

    `samples` are the weeks the board worked. With `flags`, each run also resamples the board's stall
    flags, and a week drawn as a stall finishes one of `stalled`, or nothing when there is none.
    Resampling per run carries the doubt about the pace itself, so a short history gives a wide range.
    A resample with no finished week, or with no week the board worked, is drawn again, since it would
    never finish. A percentile that reaches `HORIZON` is None, since no run measured it; the whole
    forecast is None when nothing is left, no week in `samples` finished anything, or `LIKELY`
    reaches `HORIZON`.
    """
    if items + unsplit == 0 or not any(samples):
        return None
    rng = random.Random(seed)
    stalling = bool(flags) and any(flags)
    all_stalls = stalling and all(flags)
    taken = []
    for _ in range(runs):
        left = items + sum(rng.choice(sizes) for _ in range(unsplit))
        run_samples = _resample(rng, samples, any)
        run_flags = _resample(rng, flags or [], lambda drawn: all_stalls or not all(drawn)) if stalling else []
        weeks = 0
        # Shares such as thirds leave a floating residue above zero, so done is within a billionth.
        while left > RESIDUE and weeks < HORIZON:
            if stalling and rng.choice(run_flags):
                left -= rng.choice(stalled) if stalled else 0
            else:
                left -= rng.choice(run_samples)
            weeks += 1
        taken.append(weeks)
    taken.sort()
    found = {p: _percentile(taken, p) for p in PERCENTILES}
    if found[LIKELY] >= HORIZON:
        return None
    return {p: None if weeks >= HORIZON else weeks for p, weeks in found.items()}


@dataclass(frozen=True)
class Outlook:
    """One forecast in weeks and the history it was drawn from; without one, None weeks and a `reason`."""

    left: int
    early_weeks: int | None
    likely_weeks: int | None
    worst_weeks: int | None
    weeks: int
    per_week: float
    stalls: int
    board_weeks: int
    share: float | None
    unsplit: int
    split_size: float
    split_basis: str | None
    measured: int
    reason: str | None


def _short(samples: list[int], working: list[int], settled: bool) -> str | None:
    """Why the weeks cannot pace a forecast yet, or None when they can."""
    if not settled:
        return THIN
    if not any(samples):
        return IDLE
    if sum(samples) < PACED:
        return FEW
    if not any(working):
        return IDLE
    return FLAT if len(set(working)) == 1 else None


def outlook(read: fleet.Fleet, feature: str, today: date, seed: int = 0) -> Outlook:
    """The forecast over what is left of `feature`, drawn from its own stories, archived ones too."""
    every = [*read.stories, *read.archived]
    return _outlook(every, fleet.members(every, feature), today, seed, shared=True)


def board(read: fleet.Fleet, today: date, seed: int = 0) -> Outlook:
    """The forecast over every open story the process owns, drawn from every one it finished, archived ones too."""
    every = [*read.stories, *read.archived]
    return _outlook(every, _held(every), today, seed, shared=False)


def _outlook(every: list[fleet.Story], stories: list[fleet.Story], today: date, seed: int, shared: bool) -> Outlook:
    items, unsplit = _left(stories)
    samples, settled = _paced(stories, today)
    board_counts = _paced(_held(every), today)[0]
    flags = stalls(board_counts)
    working, stalled = _split(samples, flags)
    measured = splits(every)
    basis, sizes = _sizes(measured)
    short = _short(samples, working, settled)
    found = None if short else simulate(items, unsplit, working, sizes, flags, stalled, seed=seed)
    reason = None if found is not None or not items + unsplit else short or FAR
    return Outlook(
        left=items + unsplit,
        early_weeks=None if found is None else found[EARLY],
        likely_weeks=None if found is None else found[LIKELY],
        worst_weeks=None if found is None else found[WORST],
        weeks=len(working),
        per_week=round(statistics.fmean(working), 2) if working else 0.0,
        stalls=sum(flags),
        board_weeks=len(flags),
        share=_share(samples, flags, board_counts) if shared else None,
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


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def row(label: str, weeks: int, note: str) -> str:
    unit = "week" if weeks == 1 else "weeks"
    return f"  {label:<22}{weeks:>3} {unit:<5}   {note}"


def range_rows(likely: int, early: int) -> list[str]:
    """The likely and the early weeks, as the feature and the board forecast both print them."""
    return [
        row("Likely done by", likely, f"{LIKELY} in 100 runs"),
        row("Possibly as early as", early, "half the runs"),
    ]


def stall_line(found: Outlook) -> list[str]:
    """The line that says how often the board stalled, or none when it never did."""
    if not found.stalls:
        return []
    verb = "was a stall" if found.stalls == 1 else "were stalls"
    weeks = plural(found.board_weeks, "week")
    return [f"  {found.stalls} of the board's last {weeks} {verb}, under half its usual finishes."]


def rows(found: Outlook) -> list[str]:
    """The likely and the early weeks, and what the pace was measured from."""
    if found.left == 0:
        return ["  nothing left to forecast"]
    header = f"  {plural(found.left, 'piece')} left"
    if found.early_weeks is None or found.likely_weeks is None:
        return [header, "", f"  {_WHY[found.reason or IDLE]}"]
    lines = [
        header,
        "",
        *range_rows(found.likely_weeks, found.early_weeks),
        "",
        f"  Paced by this feature's {plural(found.weeks, 'working week')}, "
        f"an average of {found.per_week:.1f} stories a week.",
        *stall_line(found),
    ]
    if found.share is not None:
        percent = round(found.share * 100)
        lines.append(f"  This feature took {percent} percent of the board's finishes in its working weeks.")
    if found.unsplit:
        lines.append(drafts(found))
    return lines


def drafts(found: Outlook) -> str:
    """How the drafts not yet split were counted, as one line."""
    drafts = f"  {plural(found.unsplit, 'draft')} not yet split, {'each ' if found.unsplit > 1 else ''}counted as"
    parked = plural(found.measured, "finished parked feature")
    if found.split_basis == "measured":
        return f"{drafts} {found.split_size:.1f} stories, the average of {parked}."
    if found.split_basis == "largest":
        size = int(found.split_size)
        return f"{drafts} {size} {'story' if size == 1 else 'stories'}, the largest of {parked}."
    return f"{drafts} 1 story, since no parked feature has finished yet."
