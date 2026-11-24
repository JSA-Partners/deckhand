from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Mapping

COST = "X-Complexity"
LIMIT = "X-RateLimit-Complexity-Limit"
REMAINING = "X-RateLimit-Complexity-Remaining"
RESET = "X-RateLimit-Complexity-Reset"
RETRY_AFTER = "Retry-After"
LIMITED = "RATELIMITED"
UNKNOWN_WAIT = 300.0
_NAME = re.compile(r"(?:query|mutation)\s+(\w+)")

Headers = Mapping[str, str]


def name_of(document: str) -> str:
    found = _NAME.search(document)
    return found.group(1) if found else "anonymous"


def _number(headers: Headers, key: str) -> float | None:
    value = headers.get(key)
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


# Linear sends the reset as epoch milliseconds; a value that small is already seconds.
def _seconds(stamp: float) -> float:
    return stamp / 1000 if stamp > 1e11 else stamp


class Meter:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._budget: dict = {}
        self._costs: dict[str, int] = {}
        self._fresh = False

    def heard(self, name: str, headers: Headers, limited: bool) -> float | None:
        now = self._clock()
        cost, limit, remaining = (_number(headers, key) for key in (COST, LIMIT, REMAINING))
        reset, retry = _number(headers, RESET), _number(headers, RETRY_AFTER)
        with self._lock:
            budget = dict(self._budget)
            for key, value in (("limit", limit), ("remaining", remaining), ("cost", cost)):
                if value is not None:
                    budget[key] = int(value)
            if reset is not None:
                budget["reset"] = _seconds(reset)
            budget["at"], budget["until"] = now, None
            if limited:
                held = budget.get("reset")
                wait = retry if retry is not None else held - now if held is not None and held > now else UNKNOWN_WAIT
                budget["until"] = now + wait
            if cost is not None:
                self._costs.setdefault(name, int(cost))
            self._budget, self._fresh = budget, True
            return budget["until"]

    def taken(self) -> dict | None:
        with self._lock:
            if not self._fresh:
                return None
            self._fresh = False
            return {"budget": dict(self._budget), "costs": dict(self._costs)}
