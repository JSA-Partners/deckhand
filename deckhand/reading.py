from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from deckhand import claims, config, features, fleet, order, queue, stage, startable
from deckhand.features import Feature, Phase
from deckhand.policy import Policy
from deckhand.step import Refusal
from deckhand.store import Store


@dataclass(frozen=True)
class Reading:
    team: str
    feature: Feature | None
    me: str | None
    waiting: list[queue.Item]
    facts: dict
    notes: list[str]


def read(
    store: Store,
    person: Callable[[], Store],
    team: str,
    fleet_read: fleet.Fleet,
    local: config.LocalConfig,
    today: date,
    quiet: frozenset[str] = frozenset(),
) -> Reading:
    notes = [fleet_read.note] if fleet_read.note else []
    feature = _current(store, team, notes)
    me = _me(person, notes)
    waiting = _waiting(store, team, feature, me, fleet_read, local, notes, quiet)
    facts = _facts(store, feature, waiting, fleet_read, me, local, today, notes)
    return Reading(team, feature, me, waiting, facts, notes)


def _current(store: Store, team: str, notes: list[str]) -> Feature | None:
    try:
        return features.current(store, team)
    except Refusal as error:
        notes.append(str(error))
    return None


def _me(person: Callable[[], Store], notes: list[str]) -> str | None:
    try:
        return claims.me(person())
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


def policy_for(notes: list[str], clones: dict[str, Path] | None = None) -> Callable[[dict], Policy]:
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


def _waiting(
    store: Store,
    team: str,
    feature: Feature | None,
    me: str | None,
    read: fleet.Fleet,
    local: config.LocalConfig,
    notes: list[str],
    quiet: frozenset[str],
) -> list[queue.Item]:
    if me is None:
        return []
    return queue.items(store, team, feature, me, read, policy_for(notes, local.clones), quiet=quiet)


def _facts(
    store: Store,
    feature: Feature | None,
    waiting: list[queue.Item],
    read: fleet.Fleet,
    me: str | None,
    local: config.LocalConfig,
    today: date,
    notes: list[str],
) -> dict:
    top = waiting[0] if waiting else None
    facts = empty(notes, read, local) | {
        "top": {"lines": top.lines, "next": top.next} if top else None,
        "feature": feature.project["name"] if feature is not None else None,
        "release": feature is not None and features.phase(feature) is Phase.RELEASED,
    }
    if feature is not None:
        facts["budget"] = _budget(feature, today)
        facts["order"], facts["cycle"] = _build_order(store, feature, me)
        mine = [s for s in feature.live if me is not None and s["assignee"] == me]
        facts["wake_due"] = [s["identifier"] for s in mine if startable.wake_due(feature, s) is not None]
    return facts


def empty(notes: list[str], read: fleet.Fleet, local: config.LocalConfig) -> dict:
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
