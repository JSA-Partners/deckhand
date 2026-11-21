from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from deckhand import answers, brief, claims, config, features, fleet, git, order, queue, stage, startable, status
from deckhand.credentials import Credentials
from deckhand.features import Feature, Phase
from deckhand.linear.client import LinearError
from deckhand.linear.connect import connect
from deckhand.policy import Policy
from deckhand.records import iso
from deckhand.step import Refusal, register, team_key
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = "captain takes [--team-key KEY]; context [--count] [--json]"


def _parse(args: list[str]) -> dict:
    found: dict = {"count": False, "json": False}
    rest = iter(args)
    for arg in rest:
        if arg in ("--count", "--json"):
            found[arg[2:]] = True
        elif arg == "--team-key":
            found["team_key"] = next(rest, "")
        else:
            raise Refusal(f"unknown argument {arg}; {USAGE}")
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


def _build_order(store: Store, feature: Feature) -> tuple[list[dict], list[str]]:
    edges = order.edges(store, feature)
    by_id = {s["id"]: s for s in feature.live}
    placed, cycle = order.build_order(feature.live, edges)
    rows = [
        {
            "ref": s["identifier"],
            "title": s["title"],
            "state": s["state"],
            "owner": s["assignee_name"],
            "blocked_by": _blockers(s, edges, by_id),
            "stage": stage.as_data(stage.of(s, feature.records.get(s["id"], []))),
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
        self._store = store
        self._runner = runner
        self._person = person
        self._today = today
        self._clock = clock

    def _app(self) -> Store:
        if self._store is not None:
            return self._store
        return LinearStore(connect(Credentials.load(config.config_dir()), "app"))

    def _person_store(self) -> Store:
        return self._person if self._person is not None else claims.person_store()

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        team = team_key(parsed)
        store, read = self._app(), fleet.sessions(self._runner)
        notes = [read.note] if read.note else []
        local = _local(notes)
        try:
            feature = _current(store, team, notes)
            me = self._me(notes)
            waiting = self._waiting(store, team, feature, me, read, local, notes)
            facts = self._facts(store, feature, waiting, read, me, local, notes)
        except LinearError as error:
            notes.append(str(error))
            facts = self._unreachable(read, local, notes, write=not parsed["count"])
        else:
            if not parsed["count"]:
                self._write(team, facts, waiting, read, notes)
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
        elif parsed["count"]:
            print(facts["count"])
        else:
            _print(facts)

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

    def _write(self, team: str, facts: dict, waiting: list[queue.Item], read: fleet.Fleet, notes: list[str]) -> None:
        state = answers.state_dir()
        _save(state, status.snapshot(self._now(), team, facts, waiting, read.sessions, status.read(state)), notes)

    def _unreachable(self, read: fleet.Fleet, local: config.LocalConfig, notes: list[str], write: bool) -> dict:
        state = answers.state_dir()
        previous = status.read(state)
        facts = _empty(notes, read, local) | {"unreachable": True, "count": "Linear unreachable; no earlier state"}
        if previous is None:
            return facts
        if write:
            _save(state, status.stale(previous, self._now()), notes)
        shown = brief.age(brief.elapsed(previous["written_at"], self._clock()))
        facts["count"] = f"Linear unreachable; showing the state as of {shown} ago"
        held = {key: previous[key] for key in ("feature", "budget", "release")}
        return facts | held | {"order": previous["stories"]}

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
            "count": queue.count_line(waiting),
            "top": {"lines": top.lines, "next": top.next} if top else None,
            "feature": feature.project["name"] if feature is not None else None,
            "release": feature is not None and features.phase(feature) is Phase.RELEASED,
        }
        if feature is not None:
            facts["budget"] = _budget(feature, self._today())
            facts["order"], facts["cycle"] = _build_order(store, feature)
            mine = [s for s in feature.live if me is not None and s["assignee"] == me]
            facts["wake_due"] = [s["identifier"] for s in mine if startable.wake_due(feature, s) is not None]
        return facts


def _save(state: Path, found: dict, notes: list[str]) -> None:
    try:
        status.write(state, found)
    except OSError as error:
        notes.append(f"cannot write {state / status.FILE}: {error.strerror or error}")


def _empty(notes: list[str], read: fleet.Fleet, local: config.LocalConfig) -> dict:
    return {
        "notes": notes,
        "unreachable": False,
        "count": "",
        "top": None,
        "slots": {"used": fleet.slots_used(read.sessions), "cap": local.slot_cap},
        "feature": None,
        "budget": None,
        "order": [],
        "cycle": [],
        "release": False,
        "watch": [s.name for s in _held(read) if fleet.busy(s)],
        "wake_due": [],
    }


def _held(read: fleet.Fleet) -> list[fleet.Session]:
    return [s for _, s in [*sorted(fleet.hands(read.sessions).items()), *sorted(fleet.spikes(read.sessions).items())]]


def _budget_line(budget: dict) -> str:
    line = f"{budget['feature']}: day {budget['days_used']} of {budget['budget_days']}"
    return line + ", planning has passed half of the week" if budget["planning_note"] else line


def _order_line(row: dict) -> str:
    state = f"{row['ref']} {row['state']} {row['owner'] or 'unassigned'}"
    return f"{state} blocked by {', '.join(row['blocked_by'])}" if row["blocked_by"] else f"{state} ready"


def _print(facts: dict) -> None:
    for note in facts["notes"]:
        print(note)
    print(facts["count"])
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
