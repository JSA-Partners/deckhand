from __future__ import annotations

from dataclasses import dataclass

from deckhand.linear.meter import clock
from deckhand.step import Refusal
from deckhand.store.local import default_path
from deckhand.store.state import LocalState

KEY = "linear-budget"
LOW_SHARE, LOW_EVERY, POKE_GAP = 0.25, 300.0, 30.0


@dataclass(frozen=True)
class Budget:
    limit: float | None = None
    remaining: float | None = None
    reset: float | None = None
    until: float | None = None

    def limited(self, now: float) -> bool:
        return self.until is not None and now < self.until

    def low(self, now: float) -> bool:
        if self.limited(now) or not self.limit or self.remaining is None:
            return False
        return (self.reset is None or now < self.reset) and self.remaining < LOW_SHARE * self.limit


def due(found: Budget, now: float, last: float) -> bool:
    if found.limited(now):
        return False
    return not found.low(now) or now - last >= LOW_EVERY


def phrase(found: Budget, now: float) -> str:
    if found.limited(now) and found.until is not None:
        return f"Linear paused until {clock(found.until)}"
    return "Linear budget low" if found.low(now) else ""


def current() -> Budget:
    try:
        with LocalState(default_path()) as rows:
            return kept(rows)
    except Refusal:
        return Budget()


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
