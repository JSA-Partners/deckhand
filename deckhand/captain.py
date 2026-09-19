"""The captain: one reading of every story, every working session, the build order, and what slipped.

`context` prints four blocks and never fails, because it is what an open session reruns every time a
person asks where things stand; `--only` prints one of them alone. `apply` writes three things and no
more: the order onto the board, a Status the board holds that the story's log does not allow, and a
blocker added to or dropped from a boarded story. Everything else it finds is reported with the
command that would fix it, because every other write is a step's, and a step is reached through
`next`.
"""

from __future__ import annotations

import argparse
import math
import os
import statistics

from deckhand import board, checklist, config, fields, fleet, forecast, gh, issue, sessions
from deckhand.config import Settings
from deckhand.step import (
    Refusal,
    block,
    indented,
    issue_number,
    issue_ref,
    open_issue,
    reason,
    ref_label,
    settings_or_error,
    step,
    usable,
)

WINDOW = 24.0
# `new` takes a whole idea as its argument, and the table is read across, not down.
COMMAND = 28


def _name(repo: str) -> str:
    return repo.partition("/")[2] or repo


def _repos(found: list[fleet.Story]) -> dict[str, str]:
    """The bare name of every repository the board holds, mapped to its slug, for matching a session."""
    return {_name(slug): slug for slug in {story.repo for story in found if story.repo}}


def _since(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _fleet_rows(read: fleet.Fleet) -> list[str]:
    rows = ["| # | Repo | Title | Status | Pts | Note |", "| --- | --- | --- | --- | --- | --- |"]
    for story in read.stories:
        if story.status == fleet.DONE and story.closed:
            continue
        note = fleet.note(story, read.blockers.get(story.key) or [], story.key in read.behind)
        points = "-" if story.points is None else str(story.points)
        title = story.title.replace("|", "\\|")
        rows.append(f"| {story.number} | {_name(story.repo)} | {title} | {story.status} | {points} | {note} |")
    return rows if len(rows) > 2 else ["  nothing on the board"]


def _ranked(read: fleet.Fleet) -> list[fleet.Ranked]:
    return fleet.order([story for story in read.stories if story.status == "Backlog"], read.blockers)


def _order_rows(read: fleet.Fleet) -> list[str]:
    ranked = _ranked(read)
    if not ranked:
        return ["  nothing in Backlog"]
    rows = ["| Rank | # | Repo | Pts | Why |", "| --- | --- | --- | --- | --- |"]
    for place, row in enumerate(ranked, start=1):
        points = "-" if row.story.points is None else str(row.story.points)
        rows.append(f"| {place} | {row.story.number} | {_name(row.story.repo)} | {points} | {row.why} |")
    return rows


def _next_line(read: fleet.Fleet, pulses: list[sessions.Pulse]) -> str:
    """The top-ranked story of each repository, and whether a session is open there to run it."""
    free = {beat.repo for beat in pulses if beat.story == sessions.FREE}
    seen: dict[str, str] = {}
    for row in _ranked(read):
        if row.story.repo in seen or row.why.startswith("waits"):
            continue
        where = "a session is free" if row.story.repo in free else "no session open"
        seen[row.story.repo] = f"{_name(row.story.repo)} {row.story.number} ({where})"
    return "Next per repository: " + (", ".join(seen.values()) if seen else "nothing ready")


def _thousands(count: int) -> str:
    return f"{count / 1000:.0f}k" if count >= 1000 else str(count)


def _short(command: str) -> str:
    return command if len(command) <= COMMAND else command[: COMMAND - 3].rstrip() + "..."


UNKNOWN_LIVE = "  Running sessions could not be read; these are transcripts from the last {hours:g} hours."


def _session_rows(pulses: list[sessions.Pulse], since: float) -> list[str]:
    head = [] if sessions.running() is not None else [UNKNOWN_LIVE.format(hours=since), ""]
    if not pulses:
        return [*head, "  none"]
    rows = [
        *head,
        "| id | Repo | Story | Last command | Idle | Tokens | Waiting | Version |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for beat in pulses:
        rows.append(
            f"| {beat.label} | {_name(beat.repo)} | {beat.story} | {_short(beat.command)} | "
            f"{_since(beat.idle)} | {_thousands(beat.tokens)} | {'yes' if beat.waiting else 'no'} | "
            f"{beat.version or '-'} |"
        )
    return rows


def _setup_rows(settings: Settings, repo: str) -> list[str]:
    """A row per thing the board still needs, so an update does not leave a person guessing.

    An item that could not be read is not reported: unreadable is not the same as missing, and
    sending a person to setup over a failed read would cost them a run for nothing.
    """
    try:
        fields, unread = gh.project_fields(settings), None
    except Exception as error:
        fields, unread = None, reason(error)
    owed = [item for item in checklist.checklist(settings, repo, fields, unread) if item.left and not item.unknown]
    return [f"| - | {_name(repo)} | {item.name}: {item.left} | run /deckhand:setup |" for item in owed]


def _anomaly_rows(settings: Settings, read: fleet.Fleet, pulses: list[sessions.Pulse]) -> list[str]:
    found = fleet.anomalies(read.stories, read.blockers, read.behind, pulses, read.missing)
    rows = [f"| {item.number} | {_name(item.repo)} | {item.what} | {item.fix} |" for item in found]
    rows += _setup_rows(settings, gh.repo_slug())
    if not rows:
        return ["  nothing out of place"]
    return ["| # | Repo | What | Fix |", "| --- | --- | --- | --- |", *rows]


BLOCKS = ("fleet", "order", "sessions", "anomalies")
ON_REQUEST = ("forecast",)  # a simulation is not worth paying for on every rerun of the context
THIN = 10  # samples in a band below which a percentile is a fit to noise, so the worst run is used
HOURS_A_DAY = 24.0  # the durations are elapsed wall clock, including the hours a story waited


def _configure_context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", metavar="ID", help="read one session properly, by the id in the table")
    parser.add_argument("--since", type=float, default=WINDOW, help="how many hours back to look for sessions")
    parser.add_argument("--only", choices=(*BLOCKS, *ON_REQUEST), help="print one block instead of all four")
    parser.add_argument(
        "--sessions", type=int, help="sessions to forecast across; defaults to the ones the fleet currently sees"
    )


def _pulses(read: fleet.Fleet, since: float) -> list[sessions.Pulse]:
    return sessions.discover(_repos(read.stories), since, os.environ.get("CLAUDE_CODE_SESSION_ID", ""))


def _days(hours: float) -> int:
    return math.ceil(hours / HOURS_A_DAY)


def _forecast_row(label: str, hours: float, note: str) -> str:
    return f"  {label:<12}{_days(hours):>3} days   {note}"


def _forecast_rows(read: fleet.Fleet, sessions_count: int) -> list[str]:
    """The floor, the commitment, and the control that pools points away, over what is not yet Done."""
    left = [story for story in read.stories if not story.closed]
    if not left:
        return ["  nothing left to forecast"]

    points = sum(story.points or 0 for story in left)
    session_word = "session" if sessions_count == 1 else "sessions"
    story_word = "story" if len(left) == 1 else "stories"
    header = f"  {len(left)} {story_word}, {points} points, {sessions_count} {session_word}"

    history = forecast.durations(read.stories)
    if not history:
        return [f"{header}: no finished stories, so no Floor and no Commitment."]

    pooled = sorted(hours for band in history.values() for hours in band)
    thin = any(len(band) < THIN for band in history.values())
    commitment_p = 100 if thin else 85
    worst_p = max(commitment_p, 95)  # never below the commitment, so a thin history cannot invert the two
    medians = {band: statistics.median(hours) for band, hours in history.items()}

    floor_hours = forecast.floor(left, read.blockers, sessions_count, medians)
    banded = forecast.simulate(left, read.blockers, sessions_count, history)
    unbanded = forecast.simulate(left, read.blockers, sessions_count, {None: pooled})
    commitment_note = ("worst run" if thin else "85th percentile") + ", banded by points"

    rows = [
        header,
        "",
        _forecast_row("Floor", floor_hours, "critical path, nothing stalls"),
        _forecast_row("Commitment", banded[commitment_p], commitment_note),
        _forecast_row("Worst seen", banded[worst_p], f"{worst_p}th percentile"),
        "",
        _forecast_row("Unbanded", unbanded[commitment_p], "the same, points ignored"),
        "",
        f"  From {len(pooled)} finished stories, the longest {pooled[-1]:.1f} hours against a median "
        f"of {statistics.median(pooled):.1f}.",
    ]
    if thin:
        rows.append(f"  Thin history: under {THIN} in a band, so the commitment is the worst run, not a fit.")
    return rows


def _one_session(pulses: list[sessions.Pulse], label: str) -> int:
    """One session read properly: its line, the last prompt a person typed, and what has been said since."""
    beat = next((found for found in pulses if found.label == label), None)
    if beat is None:
        print(f"no session {label}; run captain context for the ones there are")
        return 0
    read = sessions.deep(beat.path)
    spent = _thousands(beat.tokens)
    print(f"Session {beat.label}: {beat.repo} story {beat.story}, idle {_since(beat.idle)}, {spent} tokens")
    print(f"Last prompt: {read.prompt or 'none'}")
    print()
    print("Since then:")
    for line in indented(read.lines, "nothing"):
        print(line)
    return 0


def context(args: argparse.Namespace) -> int:
    """Read the fleet: every story, every session, the build order, and anything out of place."""
    settings = usable(settings_or_error())
    with gh.cached():  # a context reads and never writes, so one fact is read once
        read = fleet.read(settings)
        pulses = _pulses(read, args.since)
        if args.session:
            return _one_session(pulses, args.session)
        wanted = (args.only,) if args.only else BLOCKS
        print(f"Project: {settings.owner} #{settings.project}")
        print()
        if "fleet" in wanted:
            block("## Fleet", lambda: _fleet_rows(read))
            print()
        if "order" in wanted:
            block("## Order", lambda: [*_order_rows(read), "", _next_line(read, pulses)])
            print()
        if "sessions" in wanted:
            block("## Sessions", lambda: _session_rows(pulses, args.since))
            print()
        if "anomalies" in wanted:
            block("## Anomalies", lambda: _anomaly_rows(settings, read, pulses))
            print()
        if "forecast" in wanted:
            sessions_count = (
                args.sessions
                if args.sessions is not None
                else max(len([beat for beat in pulses if beat.live]) or len(pulses), 1)
            )
            block("## Forecast", lambda: _forecast_rows(read, sessions_count))
    return 0


def _configure_apply(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--order", action="store_true", help="write the build order onto the board")
    parser.add_argument("--repair", action="store_true", help="set every Status the board holds that its log forbids")
    parser.add_argument("--block", type=issue_number, metavar="N", help="the boarded story that gains a blocker")
    parser.add_argument("--unblock", type=issue_number, metavar="N", help="the boarded story that loses one")
    parser.add_argument("--by", metavar="REF", help="the blocker, owner/name#M or M for this repository")


def _order_writes(settings: Settings, read: fleet.Fleet) -> int:
    ranked = _ranked(read)
    if len(ranked) < 2:
        raise Refusal("fewer than two stories in Backlog, so there is no order to write")
    project = gh.project_id(settings)
    after: str | None = None
    for row in ranked:
        board.move(project, row.story.item, after)
        after = row.story.item
    print(f"Ordered {len(ranked)} stories")
    print("A view with its own sort shows that sort rather than the board order.")
    return len(ranked)


def _repair_writes(settings: Settings, read: fleet.Fleet) -> int:
    wrong = [
        (story, fleet.allowed(story)[0])
        for story in read.stories
        if story.status and story.status not in fleet.allowed(story)
    ]
    if not wrong and not read.missing:
        raise Refusal(
            f"nothing to repair: every Status of the {len(read.stories)} stories agrees with its log, "
            "and no drafted issue is off the board"
        )
    for story, want in wrong:
        # Several stories may be put right in one run, so the line names which one rather than
        # returning the bare `Status=X` a step prints about the story a person already named.
        fields.set_field(settings, story.repo, story.number, "Status", want)
        print(f"{story.repo}#{story.number} Status {want}")
    for repo, number, url in read.missing:
        board.add(settings, url)
        print(f"Added {repo}#{number} to the board")
    return len(wrong) + len(read.missing)


def _ref(by: str, repo: str) -> tuple[str, int]:
    try:
        return issue_ref(by, repo)
    except ValueError as error:
        raise Refusal(str(error)) from error


def _block_writes(repo: str, number: int, by: str) -> None:
    where, blocker = _ref(by, repo)
    if (where, blocker) == (repo, number):
        raise Refusal(f"#{number} cannot block itself")
    open_issue(where, blocker, repo)
    issue.add_dependency(repo, number, (where, blocker))
    print(f"#{number} blocked by {ref_label(where, blocker, repo)}")


def _unblock_writes(repo: str, number: int, by: str) -> None:
    where, blocker = _ref(by, repo)
    if (where, blocker) == (repo, number):
        raise Refusal(f"#{number} cannot block itself")
    open_issue(where, blocker, repo)
    issue.remove_dependency(repo, number, (where, blocker))
    print(f"#{number} no longer blocked by {ref_label(where, blocker, repo)}")


@step("captain", _configure_apply, issue_bound=False, configure_context=_configure_context)
def apply(args: argparse.Namespace) -> int:
    """Write the build order, set a Status the log allows, or add or drop a blocker on a boarded story."""
    if args.block is not None and args.unblock is not None:
        raise Refusal("--block and --unblock are one at a time, not both")
    if (args.block is not None or args.unblock is not None) and not args.by:
        raise Refusal("--block and --unblock each need --by, naming the blocker")
    if not args.order and not args.repair and args.block is None and args.unblock is None:
        raise Refusal("say what to write: --order, --repair, --block, or --unblock")
    if args.block is not None:
        _block_writes(gh.repo_slug(), args.block, args.by)
    if args.unblock is not None:
        _unblock_writes(gh.repo_slug(), args.unblock, args.by)
    if args.order or args.repair:
        settings = config.load()
        read = fleet.read(settings)
        if args.order:
            _order_writes(settings, read)
        if args.repair:
            _repair_writes(settings, read)
    return 0
