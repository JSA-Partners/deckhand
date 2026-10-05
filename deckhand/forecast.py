"""How long finished stories took, and when everything left on the board will be done.

Nothing here reads GitHub or prints: it takes the fleet and today's date and returns the lines of the
captain's Forecast block. The weeks come from `throughput`, the board's weekly finishes drawn the way
a feature's are, because a week's count already holds new work, new blockers and waiting. The
durations, measured from the log, give only the critical path through the blockers, a bound no
schedule can beat.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime

from deckhand import fleet, log, order, throughput

# The durations are elapsed wall clock, including the hours a story waited.
HOURS_A_DAY = 24.0
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


def critical_path(
    stories: list[fleet.Story], blockers: fleet.Blockers, history: dict[int | None, list[float]]
) -> float | None:
    """Hours along the longest chain of blockers, each story its band's median; None with no history."""
    if not history:
        return None
    medians = {band: statistics.median(took) for band, took in history.items()}
    fallback = statistics.median(medians.values())
    finish: dict[fleet.Key, float] = {}
    for story in _placed(stories, blockers):
        finish[story.key] = _waits(story, blockers, finish) + medians.get(story.points, fallback)
    return max(finish.values(), default=0.0)


_WHY = {
    throughput.THIN: f"Work on the board began fewer than {throughput.SETTLED} weeks ago, "
    "so there is no date range yet.",
    throughput.FEW: f"Fewer than {throughput.PACED} stories finished on the board in the weeks measured, "
    "so there is no date range yet.",
    throughput.IDLE: "No story finished on the board in the weeks measured, so there is no date range.",
    throughput.FAR: "The measured pace would take over two years, so there is no date range.",
    throughput.FLAT: "The board's weekly pace has not varied yet, so a date range would be falsely precise.",
}


def _row(label: str, weeks: int, note: str) -> str:
    unit = "week" if weeks == 1 else "weeks"
    return f"  {label:<22}{weeks:>3} {unit:<5}   {note}"


def rows(read: fleet.Fleet, today: date) -> list[str]:
    """When the open stories the process owns are likely done, in weeks, and the bound the blockers set."""
    every = [*read.stories, *read.archived]
    found = throughput.board(read, today)
    if found.left == 0:
        return ["  nothing left to forecast"]
    lines = [f"  {found.left} {'story' if found.left == 1 else 'stories'} left", ""]
    if found.floor_weeks is None or found.commitment_weeks is None:
        lines.append(f"  {_WHY[found.reason or throughput.IDLE]}")
    else:
        lines += [
            _row("Likely done by", found.commitment_weeks, f"{throughput.COMMITMENT}th percentile"),
            _row("Possibly as early as", found.floor_weeks, f"{throughput.FLOOR}th percentile"),
            "",
            f"  Paced by the board's last {throughput.plural(found.weeks, 'week')}, "
            f"an average of {found.per_week:.1f} stories a week.",
        ]
    if found.unsplit:
        lines.append(throughput.drafts(found))
    left = [story for story in every if not story.closed and fleet.touched(story) and not story.dropped]
    path = critical_path(left, read.blockers, durations(every))
    if path is not None:
        days = math.ceil(path / HOURS_A_DAY)
        lines += ["", f"  Cannot finish before: {throughput.plural(days, 'day')} (critical path through the blockers)"]
    return lines
