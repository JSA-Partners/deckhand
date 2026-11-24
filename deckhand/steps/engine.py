from __future__ import annotations

import os
import shlex
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

from deckhand import answers, brief, config, engine, fleet, git, hosts, linker, status
from deckhand.poller import Poller
from deckhand.step import Refusal, register, team_key
from deckhand.store.local import default_path
from deckhand.store.state import LocalState, alive
from deckhand.worker import Worker, opened_acts, problem

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


def _holder(state_dir: Path) -> str:
    try:
        with LocalState(default_path()) as rows:
            beat = rows.heartbeat()
    except Refusal:
        beat = None
    if beat is not None:
        return f" (pid {beat[0]})"
    try:
        found = (state_dir / engine.LOCK).read_text().strip()
    except OSError:
        return ""
    return f" (pid {found})" if found.isdigit() else ""


@register("engine")
class Engine:
    def __init__(self, runner: git.Runner = git.alone, clock: Callable[[], float] = time.time) -> None:
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
            self._run(_parse(args))
            return
        shown = command()
        if shutil.which("pbcopy") is None:
            print(f"copy this: {shown}")
            return
        git.call(["pbcopy"], None, self._runner, shown)
        print(f"copied: {shown}")

    def _run(self, parsed: dict) -> None:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise Refusal("the engine needs its own terminal tab")
        team, state_dir = team_key(parsed), answers.state_dir()
        held = engine.lock(state_dir)
        if held is None:
            raise Refusal(f"the engine already runs in another tab{_holder(state_dir)}")
        from deckhand import tui

        runner, clock, link, kept = self._runner, self._clock, linker.link, linker.Kept()
        fleets = Poller(lambda: fleet.sessions(runner), engine.FLEET_EVERY, tui.fleet_failed, "deckhand-fleet", clock)

        # The first Linear read waits for the first fleet read, so a hand is never read as missing.
        def linked() -> linker.Linked:
            fleets.wait(engine.FLEET_EVERY)
            return link(team, fleets.latest, clock, kept=kept)

        linear = Poller(
            linked,
            engine.LINEAR_EVERY,
            lambda error: linker.Linked(None, None, clock(), problem(error)),
            "deckhand-linear",
            clock,
        )
        worker = Worker(lambda: opened_acts(runner))
        rows = LocalState(default_path())
        # A snapshot from an earlier engine is frozen; the captain must not ask from it.
        rows.drop(status.SNAPSHOT)
        running = engine.Engine(team, rows, fleets, linear, worker, clock, runner=runner)
        try:
            fleets.start()
            worker.start()
            linear.start()
            g = brief.glyphs(getattr(sys.stdout, "encoding", None))
            said = tui.watch(lambda: status.read(state_dir), fleets, clock, brief.colored(os.environ), g, running)
        finally:
            if running.finishing():
                print(tui.FINISHING.strip(), flush=True)
            running.close()
            held.close()
        for line in said:
            print(brief.clean(line))
