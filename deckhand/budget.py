from __future__ import annotations

from dataclasses import dataclass

from deckhand.store.state import LocalState

KEY = "linear-budget"


@dataclass(frozen=True)
class Budget:
    limit: float | None = None
    remaining: float | None = None
    reset: float | None = None
    until: float | None = None


def _number(found: dict, key: str) -> float | None:
    value = found.get(key)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def kept(state: LocalState, role: str = "app") -> Budget:
    body = state.get(KEY)
    held = body.get(role) if isinstance(body, dict) else None
    found = held.get("budget") if isinstance(held, dict) else None
    if not isinstance(found, dict):
        return Budget()
    return Budget(*(_number(found, key) for key in ("limit", "remaining", "reset", "until")))


def keep(state: LocalState, role: str, taken: dict) -> None:
    body = state.get(KEY)
    body = body if isinstance(body, dict) else {}
    held = body.get(role) if isinstance(body.get(role), dict) else {}
    budget = held.get("budget") if isinstance(held.get("budget"), dict) else {}
    costs = held.get("costs") if isinstance(held.get("costs"), dict) else {}
    if (_number(taken["budget"], "at") or 0.0) >= (_number(budget, "at") or 0.0):
        budget = taken["budget"]
    body[role] = {"budget": budget, "costs": {**costs, **taken["costs"]}}
    state.put(KEY, body)
