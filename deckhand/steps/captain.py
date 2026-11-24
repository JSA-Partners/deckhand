from __future__ import annotations

import functools
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from deckhand import (
    answers,
    board,
    brief,
    config,
    fleet,
    frame,
    git,
    hosts,
    reading,
    status,
)
from deckhand.linear.client import LinearError
from deckhand.records import iso
from deckhand.step import Refusal, register, team_key
from deckhand.store import Store
from deckhand.store.open import Stores

REFRESH_LIMIT = 120.0
NOT_RUN = 127
PLACES = ("DECKHAND_CONFIG_DIR", "DECKHAND_STATE_DIR")
USAGE = "captain takes [--team-key KEY]; context [--brief] [--json] [--line [--color]] [--board [--watch] [--no-color]]"
FLAGS = ("--brief", "--json", "--line", "--color", "--board", "--watch", "--no-color")


def _parse(args: list[str]) -> dict:
    found: dict = {flag[2:]: False for flag in FLAGS}
    rest = iter(args)
    for arg in rest:
        if arg in FLAGS:
            found[arg[2:]] = True
        elif arg == "--team-key":
            found["team_key"] = next(rest, "")
        else:
            raise Refusal(f"unknown argument {arg}; {USAGE}")
    if found["color"] and not found["line"]:
        raise Refusal(f"--color goes with --line; {USAGE}")
    if found["line"] and (found["brief"] or found["json"]):
        raise Refusal(f"--line cannot join --brief or --json; {USAGE}")
    if (found["watch"] or found["no-color"]) and not found["board"]:
        raise Refusal(f"--watch and --no-color go with --board; {USAGE}")
    if found["board"] and (found["brief"] or found["json"] or found["line"]):
        raise Refusal(f"--board cannot join --brief, --json or --line; {USAGE}")
    return found


def _local(notes: list[str]) -> config.LocalConfig:
    try:
        return config.local()
    except Refusal as error:
        notes.append(str(error))
        return config.LocalConfig()


@register("captain")
class Captain:
    def __init__(
        self,
        store: Store | None = None,
        runner: git.Runner = git.run,
        *,
        person: Store | None = None,
        today: Callable[[], date] = date.today,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._stores = Stores(app=store, person=person)
        self._runner = runner
        self._today = today
        self._clock = clock

    def _app(self) -> Store:
        return self._stores("app")

    def _person_store(self) -> Store:
        return self._stores("person")

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        if parsed["line"]:
            self._line(parsed["color"])
            return
        if parsed["board"]:
            self._board(parsed["watch"], not parsed["no-color"] and brief.colored(os.environ))
            return
        team = team_key(parsed)
        store, read = self._app(), fleet.sessions(self._runner)
        notes = [read.note] if read.note else []
        local = _local(notes)
        state, alert = answers.state_dir(), None
        try:
            found = reading.read(store, self._person_store, team, read, local, self._today())
        except LinearError as error:
            notes.append(str(error))
            shown = status.read(state)
            shown = status.stale(shown, self._now()) if shown is not None else None
            facts = _stale(reading.empty(notes, read, local), shown)
        else:
            notes += found.notes[1 if read.note else 0 :]
            facts = found.facts | {"notes": notes}
            previous, unknown = status.read(state), found.me is None
            shown = status.snapshot(self._now(), team, facts, found.waiting, read.sessions, previous, unknown)
            alert = brief.notify(shown, self._clock()) if parsed["brief"] and not parsed["json"] else None
            shown["notified"] += alert[1] if alert else []
        if shown is not None:
            _save(state, shown, notes)
        facts["brief"] = brief.text(shown, self._clock(), brief.glyphs(_encoding()), notes)
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        print(facts["brief"])
        if alert:
            print(f"\n{brief.NOTIFY}{alert[0]}")
        if not parsed["brief"]:
            print()
            _print(facts)
            print(f"board: {_board_command()}")

    def _line(self, color: bool) -> None:
        try:
            found = status.read(answers.state_dir())
            shown = brief.line(found, self._clock(), brief.glyphs(_encoding()), color and brief.colored(os.environ))
            if shown:
                print(shown)
        except Exception:
            return

    def _board(self, watch: bool, color: bool) -> None:
        state, g = answers.state_dir(), brief.glyphs(_encoding())
        terminal = sys.stdout.isatty()
        if watch and terminal and sys.stdin.isatty() and os.environ.get("TERM") != "dumb":
            from deckhand import tui

            fleet_read = functools.partial(fleet.sessions, _quiet(self._runner))
            tui.watch(lambda: status.read(state), fleet_read, self._clock, color, g, _full_read)
            return
        read, now = fleet.sessions(self._runner), self._clock()
        found = board.build(status.read(state), read, now, board.midnight(now), g)
        cols = min(shutil.get_terminal_size((frame.WIDTH, 24)).columns, frame.WIDTH) if terminal else frame.WIDTH
        print(frame.text(frame.render(found, g, cols), color and terminal))
        if watch:
            print("deckhand: --watch needs a terminal; printed the board once", file=sys.stderr)

    def apply(self, args: list[str]) -> None:
        raise Refusal(f"captain has no apply; {USAGE}")

    def _now(self) -> str:
        return iso(int(self._clock() * 1000))


def _save(state: Path, found: dict, notes: list[str]) -> None:
    try:
        status.write(state, found)
    except Refusal as error:
        notes.append(f"cannot write the snapshot: {error}")


def _board_command() -> str:
    launcher = config.config_dir() / hosts.LAUNCHER
    program = launcher if os.access(launcher, os.X_OK) else Path(sys.argv[0]).resolve()
    places = [f"{name}={os.environ[name]}" for name in PLACES if os.environ.get(name)]
    prefix = ["env", *places] if places else []
    return shlex.join([*prefix, str(program), "captain", "context", "--board", "--watch"])


# The board's r runs the captain's full read, which leaves the notified list to the captain's brief.
def _full_read(clock: Callable[[], float] = time.monotonic) -> Callable[[], int | None]:
    argv, quiet = [str(Path(sys.argv[0]).resolve()), "captain", "context"], subprocess.DEVNULL
    try:
        started = subprocess.Popen(argv, stdin=quiet, stdout=quiet, stderr=quiet, start_new_session=True)
    except OSError:
        return lambda: NOT_RUN
    deadline = clock() + REFRESH_LIMIT

    def done() -> int | None:
        code = started.poll()
        if code is None and clock() >= deadline:
            started.kill()
            return started.wait()
        return code

    return done


def _quiet(runner: git.Runner) -> git.Runner:
    return lambda argv, cwd, stdin: runner(argv, cwd, "" if stdin is None else stdin)


def _encoding() -> str | None:
    return getattr(sys.stdout, "encoding", None) or getattr(sys.__stdout__, "encoding", None)


def _stale(facts: dict, shown: dict | None) -> dict:
    held = {key: shown[key] for key in ("feature", "budget", "release")} | {"order": shown["stories"]} if shown else {}
    return facts | held | {"unreachable": True}


def _budget_line(budget: dict) -> str:
    line = f"{budget['feature']}: day {budget['days_used']} of {budget['budget_days']}"
    return line + ", planning has passed half of the week" if budget["planning_note"] else line


def _order_line(row: dict) -> str:
    state, blockers = f"{row['ref']} {row['state']} {row.get('owner') or 'unassigned'}", row.get("blocked_by")
    return f"{state} blocked by {', '.join(blockers)}" if blockers else f"{state} ready"


def _print(facts: dict) -> None:
    if facts["top"] is not None:
        for line in facts["top"]["lines"]:
            print(line)
        for command in facts["top"]["next"]:
            print(f"next: {command}")
    print(f"slots {facts['slots']['used']} of {facts['slots']['cap']}")
    if facts["budget"] is not None:
        print(_budget_line(facts["budget"]))
    for row in facts["order"]:
        print(_order_line(row))
    if facts["cycle"]:
        print("cycle: " + ", ".join(facts["cycle"]))
    if facts["release"]:
        print(f"ready to release: {facts['feature']}")
    if facts["watch"]:
        print("watch: " + ", ".join(facts["watch"]))
    if facts["wake_due"]:
        print("wake due: " + ", ".join(facts["wake_due"]))
