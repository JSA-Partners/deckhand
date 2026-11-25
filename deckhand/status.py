from __future__ import annotations

import json
from pathlib import Path

from deckhand import fleet, standing
from deckhand.queue import Item
from deckhand.records import content_hash
from deckhand.step import Refusal
from deckhand.store.state import LocalState

SNAPSHOT = "snapshot"
SHAPE = 1


def item_key(item: Item) -> str:
    return content_hash(json.dumps([item.kind, item.ref, item.at, item.next]))[:12]


def _item(item: Item) -> dict:
    keys = {"key": item_key(item), "kind": item.kind, "ref": item.ref, "title": item.title}
    return keys | {"since": item.at or None, "next": item.next, "head": item.head or None, "lines": item.lines}


def _hands(sessions: list[fleet.Session], idle: frozenset[str], resuming: frozenset[str]) -> list[dict]:
    held = [*sorted(fleet.hands(sessions).items()), *sorted(fleet.spikes(sessions).items())]
    return [
        {
            "name": s.name,
            "ref": ref,
            "state": s.state,
            "waiting_for": s.waiting_for,
            "started_at": s.started_at,
            "idle": s.name in idle,
            "resuming": s.name in resuming,
            "alive": fleet.alive(s),
        }
        for ref, s in held
    ]


def snapshot(
    written_at: str,
    team: str,
    facts: dict,
    waiting: list[Item],
    sessions: list[fleet.Session],
    person_unknown: bool = False,
    idle: frozenset[str] = frozenset(),
    used: int | None = None,
    resuming: frozenset[str] = frozenset(),
) -> dict:
    return {
        "shape": SHAPE,
        "written_at": written_at,
        "team": team,
        "feature": facts["feature"],
        "budget": facts["budget"],
        "slots": facts["slots"] if used is None else {**facts["slots"], "used": used},
        "items": [_item(item) for item in waiting],
        "stories": facts["order"],
        "cycle": facts.get("cycle", []),
        "hands": _hands(sessions, idle, resuming),
        "release": facts["release"],
        "stale_since": None,
        "person_unknown": person_unknown,
    }


def _state(state: Path) -> LocalState:
    return LocalState(state / "deckhand.db")


def put(rows: LocalState, status: dict) -> None:
    rows.put(SNAPSHOT, status)


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(entry, str) for entry in value)


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _maybe(value: object, kind: type) -> bool:
    return value is None or isinstance(value, kind)


def _when(value: object) -> bool:
    return isinstance(value, str) and standing.timestamp(value) is not None


def _item_ok(found: object) -> bool:
    if not isinstance(found, dict) or not all(isinstance(found.get(key), str) for key in ("key", "kind", "ref")):
        return False
    timed = found.get("since") is None or _when(found["since"])
    return timed and _maybe(found.get("head"), str) and _strings(found.get("lines", []))


def _stage_ok(found: object) -> bool:
    if found is None:
        return True
    return isinstance(found, dict) and isinstance(found.get("name"), str) and _maybe(found.get("round"), int)


def _story_ok(found: object) -> bool:
    if not isinstance(found, dict) or not all(isinstance(found.get(key), str) for key in ("ref", "state")):
        return False
    return _maybe(found.get("owner"), str) and _strings(found.get("blocked_by", [])) and _stage_ok(found.get("stage"))


def _budget_ok(found: object) -> bool:
    if found is None:
        return True
    shaped = isinstance(found, dict) and isinstance(found.get("feature"), str) and "planning_note" in found
    return shaped and _count(found.get("days_used")) and _count(found.get("budget_days"))


def _valid(found: dict) -> bool:
    slots, items, stories = found.get("slots"), found.get("items"), found.get("stories")
    return (
        found.get("shape") == SHAPE
        and _when(found.get("written_at"))
        and isinstance(slots, dict)
        and _count(slots.get("used"))
        and _count(slots.get("cap"))
        and "feature" in found
        and _maybe(found["feature"], str)
        and "budget" in found
        and _budget_ok(found["budget"])
        and isinstance(found.get("release"), bool)
        and isinstance(items, list)
        and all(_item_ok(entry) for entry in items)
        and _strings(found.get("cycle", []))
        and isinstance(stories, list)
        and all(_story_ok(entry) for entry in stories)
    )


def read(state: Path) -> dict | None:
    try:
        with _state(state) as rows:
            return get(rows)
    except Refusal:
        return None


def get(rows: LocalState) -> dict | None:
    try:
        found = rows.get(SNAPSHOT)
    except (Refusal, ValueError):
        return None
    return found if isinstance(found, dict) and _valid(found) else None


def cycle_line(cycle: list[str]) -> str:
    return f"build order cycle: {', '.join(cycle)}; break it in Linear"


def stale(previous: dict, at: str) -> dict:
    return previous | {"stale_since": previous.get("stale_since") or at}
