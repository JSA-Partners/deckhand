"""The captain: one reading of every story, every working session, the build order, and what slipped.

`context` prints six blocks and never fails, because it is what an open session reruns every time a
person asks where things stand: who is waiting, what moved since the last read, then the fleet, the
order, the sessions and the anomalies; `--only` prints one of the last four, or the forecast, alone.
`apply` writes the board and no more: the order onto it, a Status the board holds that the story's
log does not allow, the label that says a story is this process's business, and a blocker added to
or dropped from a boarded story. Everything else it finds is reported with the command that would
fix it, because every other write is a step's, and a step is reached through `next`.
"""

from __future__ import annotations

import argparse
import os
import time

from deckhand import (
    board,
    candidates,
    checklist,
    columns,
    config,
    edges,
    fleet,
    forecast,
    gh,
    order,
    sessions,
    since,
    transcript,
)
from deckhand.config import Settings
from deckhand.step import (
    Refusal,
    block,
    indented,
    issue_number,
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
    rows = ["| # | Repo | Title | Status | Pts | Who | Note |", "| --- | --- | --- | --- | --- | --- | --- |"]
    foreign = 0
    for story in read.stories:
        if story.status == columns.DONE and story.closed:
            continue
        if not fleet.touched(story):
            foreign += 1
            continue
        note = fleet.note(story, read.blockers.get(story.key) or [], story.key in read.behind)
        points = "-" if story.points is None else str(story.points)
        title = story.title.replace("|", "\\|")
        who = ", ".join(story.assignees) or "-"
        rows.append(f"| {story.number} | {_name(story.repo)} | {title} | {story.status} | {points} | {who} | {note} |")
    if len(rows) == 2:
        rows = ["  nothing of deckhand's on the board" if foreign else "  nothing on the board"]
    if foreign:
        many = foreign != 1
        rows += ["", f"{foreign} {'items' if many else 'item'} on the board {'are' if many else 'is'} not deckhand's."]
    return rows


def _story(story: str) -> str:
    return story if story == sessions.FREE else f"#{story}"


def _waiting_rows(pulses: list[sessions.Pulse]) -> list[str]:
    held = sorted((beat for beat in pulses if beat.waiting), key=lambda beat: -beat.idle)
    return [
        f"  {beat.label} {_story(beat.story)}, {_since(beat.idle)}: {transcript.asking(beat.path) or 'no words yet'}"
        for beat in held
    ] or ["  nobody"]


def _ranked(read: fleet.Fleet) -> list[order.Ranked]:
    return order.ranked([story for story in read.stories if story.status == columns.BACKLOG], read.blockers)


def _order_rows(read: fleet.Fleet) -> list[str]:
    ranked = _ranked(read)
    if not ranked:
        return ["  nothing in Backlog"]
    rows = ["| Rank | # | Repo | Pts | Why |", "| --- | --- | --- | --- | --- |"]
    for place, row in enumerate(ranked, start=1):
        points = "-" if row.story.points is None else str(row.story.points)
        rows.append(f"| {place} | {row.story.number} | {_name(row.story.repo)} | {points} | {row.why} |")
    return rows


def _head(read: fleet.Fleet, repo: str) -> str | None:
    """The open story at the top of this repository's waiting chains, and how much it holds up.

    A blocker that is not in Backlog is nowhere in the order table, so a repository whose whole
    Backlog waits has nothing to show without it.
    """
    backlog = {story.key for story in read.stories if story.status == columns.BACKLOG}
    seen: set[fleet.Key] = set()
    roots: set[fleet.Key] = set()
    stack = [(w, n) for key, holds in read.blockers.items() if key[0] == repo and key in backlog for w, n, _ in holds]
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        above = [(w, n) for w, n, _ in read.blockers.get(key) or []]
        if above:
            stack.extend(above)
        elif key not in backlog:
            roots.add(key)
    if not roots:
        return None
    waits = order.waiting(read.blockers)
    best = max(sorted(roots), key=lambda key: len(order.downstream(key, waits)))
    count = len(order.downstream(best, waits))
    story = next((row for row in read.stories if row.key == best), None)
    status = story.status if story and story.status else "off the board"
    stories = "story" if count == 1 else "stories"
    return f"{ref_label(best[0], best[1], repo)} is {status}, unblocking {count} {stories}"


def _next_line(read: fleet.Fleet, pulses: list[sessions.Pulse]) -> str:
    """Each repository's top-ranked story in the ranked order, and whether a session is open to run it."""
    free = {beat.repo for beat in pulses if beat.story == sessions.FREE}
    seen: dict[str, str] = {}
    waiting: set[str] = set()
    for row in _ranked(read):
        if row.why.startswith("waits"):
            waiting.add(row.story.repo)
            continue
        if row.story.repo in seen:
            continue
        where = "a session is free" if row.story.repo in free else "no session open"
        seen[row.story.repo] = f"{_name(row.story.repo)} {row.story.number} ({where})"
    for repo in sorted(waiting - set(seen)):
        head = _head(read, repo)
        if head is not None:
            seen[repo] = f"{_name(repo)}: {head}"
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


def _found(read: fleet.Fleet, pulses: list[sessions.Pulse]) -> list[fleet.Anomaly]:
    return fleet.anomalies(
        read.stories, read.blockers, read.behind, pulses, read.missing, read.me, archived=read.archived
    )


def _line(item: fleet.Anomaly) -> str:
    return f"{item.repo}#{item.number} {item.what}"


def _anomaly_rows(settings: Settings, found: list[fleet.Anomaly], standing: set[str]) -> list[str]:
    repo = gh.repo_slug()
    new = [item for item in found if _line(item) not in standing]
    rows = [f"| {item.number} | {_name(item.repo)} | {item.what} | {item.fix} |" for item in new]
    rows += _setup_rows(settings, repo)
    old = [f"Standing: {ref_label(item.repo, item.number, repo)} {item.what}" for item in found if item not in new]
    if not rows and not old:
        return ["  nothing out of place"]
    table = ["| # | Repo | What | Fix |", "| --- | --- | --- | --- |", *rows] if rows else []
    return [*table, *([""] if table and old else []), *old]


BLOCKS = ("fleet", "order", "sessions", "anomalies")
ON_REQUEST = ("forecast", "candidates")  # neither is worth paying for on every rerun of the context


def _configure_context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", metavar="ID", help="read one session properly, by the id in the table")
    parser.add_argument("--since", type=float, default=WINDOW, help="how many hours back to look for sessions")
    parser.add_argument("--only", choices=(*BLOCKS, *ON_REQUEST), help="print one block instead of the full read")
    parser.add_argument("--sessions", type=int, help="stories to forecast at once; defaults to what history shows")


def _pulses(read: fleet.Fleet, since: float) -> list[sessions.Pulse]:
    return sessions.discover(_repos(read.stories), since, os.environ.get("CLAUDE_CODE_SESSION_ID", ""))


def _remember(read: fleet.Fleet, pulses: list[sessions.Pulse], found: list[fleet.Anomaly]) -> dict:
    stories = {
        f"{story.repo}#{story.number}": {"status": story.status, "closed": story.closed}
        for story in read.stories
        if fleet.touched(story)
    }
    held = {beat.label: {"story": beat.story, "waiting": beat.waiting} for beat in pulses}
    return since.snapshot(stories, held, [_line(item) for item in found], time.time())


def _moved(before: dict, after: dict) -> list[str]:
    head = since.heading(before, after["at"])
    lines = since.changes(before, after, gh.repo_slug())
    return [f"  {head}:", *(f"    {line}" for line in lines)] if lines else [f"  Nothing moved {head.lower()}."]


def _one_session(pulses: list[sessions.Pulse], label: str) -> int:
    """One session read properly: its line, the last prompt a person typed, and what has been said since."""
    beat = next((found for found in pulses if found.label == label), None)
    if beat is None:
        print(f"no session {label}; run captain context for the ones there are")
        return 0
    read = transcript.deep(beat.path)
    spent = _thousands(beat.tokens)
    print(f"Session {beat.label}: {beat.repo} story {beat.story}, idle {_since(beat.idle)}, {spent} tokens")
    print(f"Last prompt: {read.prompt or 'none'}")
    print()
    print("Since then:")
    for line in indented(read.lines, "nothing"):
        print(line)
    return 0


def context(args: argparse.Namespace) -> int:
    """Read the fleet: who is waiting, what moved, every story and session, the order, and what is out of place."""
    settings = usable(settings_or_error())
    with gh.cached():  # a context reads and never writes, so one fact is read once
        read = fleet.read(settings)
        pulses = _pulses(read, args.since)
        if args.session:
            return _one_session(pulses, args.session)
        wanted = (args.only,) if args.only else BLOCKS
        print(f"Project: {settings.owner} #{settings.project}")
        print()
        previous = after = found = None
        if not args.only:
            block("## Waiting on you", lambda: _waiting_rows(pulses))
            print()
            try:
                found = _found(read, pulses)
                previous, after = since.load(since.path(settings)), _remember(read, pulses, found)
            except Exception as error:
                unread = f"  what moved could not be read ({reason(error)})"
                block("## Since you last looked", lambda: [unread])
                print()
            if previous and after:
                block("## Since you last looked", lambda: _moved(previous, after))
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
            standing = set((previous or {}).get("anomalies") or [])
            block(
                "## Anomalies",
                lambda: _anomaly_rows(settings, _found(read, pulses) if found is None else found, standing),
            )
            print()
        if "forecast" in wanted:
            live = len([beat for beat in pulses if beat.live])
            at_once, source = forecast.parallel(args.sessions, read, live=live)
            block("## Forecast", lambda: forecast.rows(read, at_once, source))
        if "candidates" in wanted:
            block("## Candidates", lambda: candidates.rows(read, gh.repo_slug()))
        if after:
            try:
                since.save(since.path(settings), after)
            except OSError as error:
                unsaved = f"  not saved, so the next read cannot say what moved ({reason(error)})"
                block("## Snapshot", lambda: [unsaved])
    return 0


def _configure_apply(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--order", action="store_true", help="write the build order onto the board")
    parser.add_argument("--repair", action="store_true", help="put right every Status, label, and item owed")
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
    """Put every story the process owns that never reached the board on it.

    Nothing else is left to repair. The column is written by a step's apply and read back by the
    same, so there is no second source for it to disagree with, and the label is what makes a story
    the process's business rather than something a repair could work out.
    """
    if not read.missing:
        raise Refusal(f"nothing to repair: every story the process owns is on the board, all {len(read.stories)}")
    for repo, number, url in read.missing:
        board.add(settings, url)
        print(f"Added {repo}#{number} to the board")
    return len(read.missing)


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
        edges.block(gh.repo_slug(), args.block, args.by)
    if args.unblock is not None:
        edges.unblock(gh.repo_slug(), args.unblock, args.by)
    if args.order or args.repair:
        settings = config.load()
        read = fleet.read(settings)
        if args.order:
            _order_writes(settings, read)
        if args.repair:
            _repair_writes(settings, read)
    return 0
