from __future__ import annotations

import os
import shlex
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

from deckhand import config, git, hosts
from deckhand.step import Refusal, register
from deckhand.store.local import default_path
from deckhand.store.state import LocalState, alive

USAGE = "engine takes [--team-key KEY]; context, apply run, or apply copy"
ACTIONS = ("run", "copy")
PLACES = ("DECKHAND_CONFIG_DIR", "DECKHAND_STATE_DIR")


def invocation(*words: str) -> str:
    launcher = config.config_dir() / hosts.LAUNCHER
    program = launcher if os.access(launcher, os.X_OK) else Path(sys.argv[0]).resolve()
    places = [f"{name}={os.environ[name]}" for name in PLACES if os.environ.get(name)]
    prefix = ["env", *places] if places else []
    return shlex.join([*prefix, str(program), *words])


def command() -> str:
    return invocation("engine", "apply", "run")


def _parse(args: list[str]) -> dict:
    found: dict = {"words": []}
    rest = iter(args)
    for arg in rest:
        if arg == "--team-key":
            found["team_key"] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    return found


def _running(now: float) -> int | None:
    try:
        with LocalState(default_path()) as rows:
            beat = rows.heartbeat()
    except Refusal:
        return None
    return beat[0] if beat is not None and alive(beat[1], now) else None


@register("engine")
class Engine:
    def __init__(self, runner: git.Runner = git.run, clock: Callable[[], float] = time.time) -> None:
        self._runner = runner
        self._clock = clock

    def context(self, args: list[str]) -> None:
        pid = _running(self._clock())
        print(f"engine: running (pid {pid})" if pid is not None else "engine: not running")
        print(f"start: {command()}")

    def apply(self, args: list[str]) -> None:
        words = _parse(args)["words"]
        if len(words) != 1 or words[0] not in ACTIONS:
            raise Refusal(f"engine apply takes run or copy; {USAGE}")
        if words == ["run"]:
            raise Refusal("the engine's screen is not built yet")
        shown = command()
        if shutil.which("pbcopy") is None:
            print(f"copy this: {shown}")
            return
        git.call(["pbcopy"], None, self._runner, shown)
        print(f"copied: {shown}")
