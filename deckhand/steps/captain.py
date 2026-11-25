from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Callable

from deckhand import answers, board, brief, config, fleet, frame, git, launch, status, watch
from deckhand.step import Refusal, register
from deckhand.store.local import default_path
from deckhand.store.state import LocalState, alive

USAGE = "captain takes context [--json] [--line [--color]] [--board [--no-color]], or apply hold|release"
ACTIONS = ("hold", "release")
FLAGS = ("--json", "--line", "--color", "--board", "--no-color")
RUNNING, STOPPED = "engine: running", "engine: not running"
UNREAD = "the engine has not read Linear yet"
OUTDATED = f"the captain's settings are out of date; {brief.SETUP}, then restart the captain"


def _parse(args: list[str]) -> dict:
    found: dict = {flag[2:]: False for flag in FLAGS} | {"words": []}
    for arg in args:
        if arg in FLAGS:
            found[arg[2:]] = True
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    if found["color"] and not found["line"]:
        raise Refusal(f"--color goes with --line; {USAGE}")
    if found["line"] and found["json"]:
        raise Refusal(f"--line cannot join --json; {USAGE}")
    if found["no-color"] and not found["board"]:
        raise Refusal(f"--no-color goes with --board; {USAGE}")
    if found["board"] and (found["json"] or found["line"]):
        raise Refusal(f"--board cannot join --json or --line; {USAGE}")
    return found


def _read() -> tuple[dict | None, float | None]:
    with LocalState(default_path()) as rows:
        beat = rows.heartbeat()
        return status.get(rows), beat[1] if beat is not None else None


@register("captain")
class Captain:
    def __init__(self, runner: git.Runner = git.run, *, clock: Callable[[], float] = time.time) -> None:
        self._runner = runner
        self._clock = clock

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        if parsed["words"]:
            raise Refusal(f"unknown argument {parsed['words'][0]}; {USAGE}")
        if parsed["line"]:
            self._line(parsed["color"])
            return
        if parsed["board"]:
            self._board(not parsed["no-color"] and brief.colored(os.environ))
            return
        now = self._clock()
        shown, beat = _read()
        facts = _facts(shown, alive(beat, now), now) | {"settings": _outdated()}
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        _print(facts)

    def _line(self, color: bool) -> None:
        try:
            found = status.read(answers.state_dir())
            shown = brief.line(found, self._clock(), brief.glyphs(_encoding()), color and brief.colored(os.environ))
            if shown:
                print(shown)
        except Exception:
            return

    def _board(self, color: bool) -> None:
        g, terminal = brief.glyphs(_encoding()), sys.stdout.isatty()
        read, now = fleet.sessions(self._runner), self._clock()
        found = board.build(status.read(answers.state_dir()), read, now, board.midnight(now), g)
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


def _encoding() -> str | None:
    return getattr(sys.stdout, "encoding", None) or getattr(sys.__stdout__, "encoding", None)


# A frozen snapshot would ask an answered item again, so a stopped engine shows no item.
def _facts(shown: dict | None, running: bool, now: float) -> dict:
    facts: dict = {"engine": RUNNING if running else STOPPED, "brief": None, "top": None, "release": None, "cycle": []}
    if not running or shown is None:
        return facts
    items = shown["items"]
    facts["brief"] = brief.text(shown, now, brief.glyphs(_encoding()))
    facts["top"] = {"lines": items[0]["lines"], "next": items[0]["next"]} if items else None
    facts["release"] = shown["feature"] if shown["release"] else None
    facts["cycle"] = shown.get("cycle", [])
    return facts


def _outdated() -> str | None:
    path = config.config_dir() / launch.CAPTAIN_SETTINGS
    try:
        installed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        installed = None
    try:
        protected = launch.protected(config.config_dir(), answers.state_dir())
        return None if launch.captain_current(installed, protected, config.local().clones.values()) else OUTDATED
    except Refusal:
        return None


def _print(facts: dict) -> None:
    if facts["brief"] is not None:
        print(facts["brief"])
        print()
    print(facts["engine"])
    if facts["engine"] == RUNNING and facts["brief"] is None:
        print(UNREAD)
    if facts["top"] is not None:
        for line in facts["top"]["lines"]:
            print(brief.clean(line))
        for command in facts["top"]["next"]:
            print(f"next: {command}")
    if facts["cycle"]:
        print(status.cycle_line(facts["cycle"]))
    if facts["release"]:
        print(f"ready to release: {brief.clean(facts['release'])}")
    if facts["settings"]:
        print(facts["settings"])
