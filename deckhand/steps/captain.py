from __future__ import annotations

import json
import os
import shutil
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
    reading,
    status,
    watch,
)
from deckhand.linear.client import LinearError
from deckhand.records import iso
from deckhand.step import Refusal, register, team_key
from deckhand.steps.engine import command, invocation
from deckhand.store import Store
from deckhand.store.local import default_path
from deckhand.store.open import Stores
from deckhand.store.state import LocalState

INSIDE = "the board now runs inside the engine"
USAGE = (
    "captain takes [--team-key KEY]; context [--brief] [--json] [--line [--color]] [--board [--watch] [--no-color]], "
    "or apply hold|release"
)
ACTIONS = ("hold", "release")
FLAGS = ("--brief", "--json", "--line", "--color", "--board", "--watch", "--no-color")


def _parse(args: list[str]) -> dict:
    found: dict = {flag[2:]: False for flag in FLAGS} | {"words": []}
    rest = iter(args)
    for arg in rest:
        if arg in FLAGS:
            found[arg[2:]] = True
        elif arg == "--team-key":
            found["team_key"] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
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
        if parsed["words"]:
            raise Refusal(f"unknown argument {parsed['words'][0]}; {USAGE}")
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
            found = reading.read(store, self._person_store, team, read, local, self._today(), notes=notes)
        except LinearError as error:
            notes.append(str(error))
            shown = status.read(state)
            shown = status.stale(shown, self._now()) if shown is not None else None
            facts = _stale(reading.empty(notes, read, local), shown)
        else:
            facts = found.facts
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
            print(f"board: {invocation('captain', 'context', '--board', '--watch')}")

    def _line(self, color: bool) -> None:
        try:
            found = status.read(answers.state_dir())
            shown = brief.line(found, self._clock(), brief.glyphs(_encoding()), color and brief.colored(os.environ))
            if shown:
                print(shown)
        except Exception:
            return

    def _board(self, watch: bool, color: bool) -> None:
        if watch:
            print(INSIDE)
            print(command())
            return
        state, g, terminal = answers.state_dir(), brief.glyphs(_encoding()), sys.stdout.isatty()
        read, now = fleet.sessions(self._runner), self._clock()
        found = board.build(status.read(state), read, now, board.midnight(now), g)
        cols = min(shutil.get_terminal_size((frame.WIDTH, 24)).columns, frame.WIDTH) if terminal else frame.WIDTH
        print(frame.text(frame.render(found, g, cols), color and terminal))

    def apply(self, args: list[str]) -> None:
        words = _parse(args)["words"]
        if words != args or len(words) != 1 or words[0] not in ACTIONS:
            raise Refusal(f"captain apply takes hold or release; {USAGE}")
        with LocalState(default_path()) as rows:
            if args == ["hold"]:
                rows.put(watch.HOLD, {})
            else:
                rows.drop(watch.HOLD)
        print("done")

    def _now(self) -> str:
        return iso(int(self._clock() * 1000))


def _save(state: Path, found: dict, notes: list[str]) -> None:
    try:
        status.write(state, found)
    except Refusal as error:
        notes.append(f"cannot write the snapshot: {error}")


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
