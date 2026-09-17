"""The captain: one reading of every story, every working session, the build order, and what slipped.

`context` prints four blocks and never fails, because it is what an open session reruns every time a
person asks where things stand. `apply` writes two things and no more: the order onto the board, and
a Status the board holds that the story's log does not allow. Everything else it finds is reported
with the command that would fix it, because every other write is a step's, and a step is reached
through `next`.
"""

from __future__ import annotations

import argparse
import os

from deckhand import board, config, fields, fleet, gh, sessions
from deckhand.config import Settings
from deckhand.step import Refusal, block, indented, settings_or_error, step, usable

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


def _short(command: str) -> str:
    return command if len(command) <= COMMAND else command[: COMMAND - 3].rstrip() + "..."


def _session_rows(pulses: list[sessions.Pulse]) -> list[str]:
    if not pulses:
        return ["  none"]
    rows = [
        "| id | Repo | Story | Last command | Idle | Cost | Waiting |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for beat in pulses:
        rows.append(
            f"| {beat.label} | {_name(beat.repo)} | {beat.story} | {_short(beat.command)} | "
            f"{_since(beat.idle)} | ${beat.cost:.2f} | {'yes' if beat.waiting else 'no'} |"
        )
    return rows


def _anomaly_rows(read: fleet.Fleet, pulses: list[sessions.Pulse]) -> list[str]:
    found = fleet.anomalies(read.stories, read.blockers, read.behind, pulses, read.missing)
    if not found:
        return ["  nothing out of place"]
    rows = ["| # | Repo | What | Fix |", "| --- | --- | --- | --- |"]
    for item in found:
        rows.append(f"| {item.number} | {_name(item.repo)} | {item.what} | {item.fix} |")
    return rows


def _configure_context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", metavar="ID", help="read one session properly, by the letter it was given")
    parser.add_argument("--since", type=float, default=WINDOW, help="how many hours back to look for sessions")


def _pulses(read: fleet.Fleet, since: float) -> list[sessions.Pulse]:
    return sessions.discover(_repos(read.stories), since, os.environ.get("CLAUDE_CODE_SESSION_ID", ""))


def _one_session(pulses: list[sessions.Pulse], label: str) -> int:
    """One session read properly: its line, the last prompt a person typed, and what has been said since."""
    beat = next((found for found in pulses if found.label == label), None)
    if beat is None:
        print(f"no session {label}; run captain context for the ones there are")
        return 0
    read = sessions.deep(beat.path)
    print(f"Session {beat.label}: {beat.repo} story {beat.story}, idle {_since(beat.idle)}, cost ${beat.cost:.2f}")
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
        print(f"Project: {settings.owner} #{settings.project}")
        print()
        block("## Fleet", lambda: _fleet_rows(read))
        print()
        block("## Order", lambda: [*_order_rows(read), "", _next_line(read, pulses)])
        print()
        block("## Sessions", lambda: _session_rows(pulses))
        print()
        block("## Anomalies", lambda: _anomaly_rows(read, pulses))
    return 0


def _configure_apply(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--order", action="store_true", help="write the build order onto the board")
    parser.add_argument("--repair", action="store_true", help="set every Status the board holds that its log forbids")


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


@step("captain", _configure_apply, issue_bound=False, configure_context=_configure_context)
def apply(args: argparse.Namespace) -> int:
    """Write the build order onto the board, or set a Status the board holds that its log forbids."""
    if not args.order and not args.repair:
        raise Refusal("say what to write: --order, --repair, or both")
    settings = config.load()
    read = fleet.read(settings)
    if args.order:
        _order_writes(settings, read)
    if args.repair:
        _repair_writes(settings, read)
    return 0
