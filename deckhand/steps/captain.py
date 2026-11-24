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
    claims,
    config,
    features,
    fleet,
    frame,
    git,
    hosts,
    order,
    queue,
    stage,
    startable,
    status,
)
from deckhand.features import Feature, Phase
from deckhand.linear.client import LinearError
from deckhand.policy import Policy
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


def _current(store: Store, team: str, notes: list[str]) -> Feature | None:
    try:
        return features.current(store, team)
    except Refusal as error:
        notes.append(str(error))
    return None


def _budget(feature: Feature, today: date) -> dict | None:
    budget = features.budget_of(feature.project, today)
    if budget is None:
        return None
    note = features.phase(feature) is Phase.PLANNING and budget.planning_note
    return {
        "feature": feature.project["name"],
        "days_used": budget.days_used,
        "budget_days": budget.budget_days,
        "planning_note": note,
    }


def _blockers(story: dict, edges: list[tuple[str, str]], by_id: dict[str, dict]) -> list[str]:
    held = [by_id[blocker] for blocker, blocked in edges if blocked == story["id"] and blocker in by_id]
    return sorted(s["identifier"] for s in held if s["state"] != order.DONE)


def _build_order(store: Store, feature: Feature, me: str | None) -> tuple[list[dict], list[str]]:
    edges = order.edges(store, feature)
    by_id = {s["id"]: s for s in feature.live}
    placed, cycle = order.build_order(feature.live, edges)
    rows = [
        {
            "ref": s["identifier"],
            "title": s["title"],
            "state": s["state"],
            "owner": s["assignee_name"],
            "mine": me is None or s["assignee"] in (None, me),
            "blocked_by": _blockers(s, edges, by_id),
            "stage": stage.as_data(stage.of(s, feature.records.get(s["id"], []))),
            "completed_at": s.get("completed_at"),
        }
        for s in placed
    ]
    return rows, cycle


def _policy_for(notes: list[str], clones: dict[str, Path] | None = None) -> Callable[[dict], Policy]:
    clones = clones if clones is not None else {}

    def policy(story: dict) -> Policy:
        clone = next((clones[label] for label in story["labels"] if label in clones), None)
        try:
            if clone is None:
                raise Refusal(f"no clone for {', '.join(story['labels'])} in {config.config_dir() / config.FILE}")
            return Policy.load(clone)
        except Refusal as error:
            if str(error) not in notes:
                notes.append(str(error))
            return Policy("")

    return policy


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
            feature = _current(store, team, notes)
            me = self._me(notes)
            waiting = self._waiting(store, team, feature, me, read, local, notes)
            facts = self._facts(store, feature, waiting, read, me, local, notes)
        except LinearError as error:
            notes.append(str(error))
            shown = status.read(state)
            shown = status.stale(shown, self._now()) if shown is not None else None
            facts = _stale(_empty(notes, read, local), shown)
        else:
            shown = status.snapshot(self._now(), team, facts, waiting, read.sessions, status.read(state), me is None)
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

    def _me(self, notes: list[str]) -> str | None:
        try:
            return claims.me(self._person_store())
        except Refusal as error:
            notes.append(str(error))
        return None

    def _now(self) -> str:
        return iso(int(self._clock() * 1000))

    def _waiting(
        self,
        store: Store,
        team: str,
        feature: Feature | None,
        me: str | None,
        read: fleet.Fleet,
        local: config.LocalConfig,
        notes: list[str],
    ) -> list[queue.Item]:
        if me is None:
            return []
        return queue.items(store, team, feature, me, read, _policy_for(notes, local.clones))

    def _facts(
        self,
        store: Store,
        feature: Feature | None,
        waiting: list[queue.Item],
        read: fleet.Fleet,
        me: str | None,
        local: config.LocalConfig,
        notes: list[str],
    ) -> dict:
        top = waiting[0] if waiting else None
        facts = _empty(notes, read, local) | {
            "top": {"lines": top.lines, "next": top.next} if top else None,
            "feature": feature.project["name"] if feature is not None else None,
            "release": feature is not None and features.phase(feature) is Phase.RELEASED,
        }
        if feature is not None:
            facts["budget"] = _budget(feature, self._today())
            facts["order"], facts["cycle"] = _build_order(store, feature, me)
            mine = [s for s in feature.live if me is not None and s["assignee"] == me]
            facts["wake_due"] = [s["identifier"] for s in mine if startable.wake_due(feature, s) is not None]
        return facts


def _save(state: Path, found: dict, notes: list[str]) -> None:
    try:
        status.write(state, found)
    except OSError as error:
        notes.append(f"cannot write {state / status.FILE}: {error.strerror or error}")


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


def _empty(notes: list[str], read: fleet.Fleet, local: config.LocalConfig) -> dict:
    return {
        "notes": notes,
        "unreachable": False,
        "top": None,
        "slots": {"used": fleet.slots_used(read.sessions), "cap": local.slot_cap},
        "feature": None,
        "budget": None,
        "order": [],
        "cycle": [],
        "release": False,
        "watch": [s.name for _, s in _held(read) if fleet.busy(s)],
        "wake_due": [],
    }


def _held(read: fleet.Fleet) -> list[tuple[str, fleet.Session]]:
    return [*sorted(fleet.hands(read.sessions).items()), *sorted(fleet.spikes(read.sessions).items())]


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
