from __future__ import annotations

from dataclasses import dataclass

from deckhand import fleet
from deckhand.reconcile import RETRY_AFTER, RETRY_CAP, Action, Launch, Remove, Resume, Stop

START, STOP, REMOVE, FINISHED_STOP = "start", "Stop", "Remove", "Stop:finished"
LAUNCH = f"{START}:Launch"


@dataclass(frozen=True)
class Stories:
    finished: frozenset[str]
    mine: frozenset[str]
    wanted: frozenset[str]


def _key(action: Action) -> tuple[str, str, str]:
    if isinstance(action, Launch | Resume):
        return f"{START}:{type(action).__name__}", action.name, ""
    if isinstance(action, Stop):
        return f"{STOP}:{action.why}", action.name, action.short
    if isinstance(action, Remove):
        return REMOVE, action.name, action.short
    return type(action).__name__, action.name, ""


class Lines:
    def __init__(self) -> None:
        self._held: dict[tuple[str, str, str], tuple[str, float]] = {}
        self._tries: dict[tuple[str, str, str], tuple[int, float]] = {}

    def say(self, key: str, line: str) -> None:
        self._held.pop((key, "", ""), None)
        self._held[(key, "", "")] = (line, 0.0)

    def clear(self, key: str) -> None:
        self._held.pop((key, "", ""), None)

    def heard(self, action: Action, line: str | None, now: float) -> None:
        key = _key(action)
        if line is None:
            self._forget(key)
            return
        self._tries[key] = (self._tries.get(key, (0, now))[0] + 1, now)
        self._held.pop(key, None)
        self._held[key] = (line, now)

    def _forget(self, key: tuple[str, str, str]) -> None:
        self._held.pop(key, None)
        self._tries.pop(key, None)

    def failed(self) -> dict[str, tuple[int, float]]:
        found: dict[str, tuple[int, float]] = {}
        for (kind, name, _), (tries, last) in self._tries.items():
            if kind.startswith(START):
                held = found.get(name, (0, last))
                found[name] = (max(held[0], tries), max(held[1], last))
        return found

    def backing_off(self, action: Action, now: float) -> bool:
        if not isinstance(action, Stop | Remove):
            return False
        tries, last = self._tries.get(_key(action), (0, now))
        return bool(tries) and now - last < min(RETRY_AFTER * 2 ** (tries - 1), RETRY_CAP)

    # Only a good fleet read, or a good Linear read, that shows the cause gone clears a failure.
    def settle(self, read: fleet.Fleet, stories: Stories | None) -> None:
        if read.note:
            return
        alive = {s.name for s in read.sessions if fleet.alive(s)}
        running = {s.id for s in read.sessions if fleet.alive(s)}
        listed = {s.id for s in read.sessions}
        for key in list(self._tries.keys() | self._held.keys()):
            kind, name, short = key
            if not name:
                continue
            if kind.startswith(START):
                gone = name in alive
            elif kind.startswith(STOP):
                gone = short not in running
            else:
                gone = kind == REMOVE and short not in listed
            if gone or (stories is not None and _settled(kind, name, stories)):
                self._forget(key)

    def newest(self) -> str:
        return next(reversed(self._held.values()))[0] if self._held else ""


def _settled(kind: str, name: str, stories: Stories) -> bool:
    if kind == LAUNCH:
        return name.startswith(fleet.HAND) and (name in stories.finished or name not in stories.mine)
    if kind.startswith(START):
        return name in stories.finished
    if kind == FINISHED_STOP:
        return name not in stories.finished
    if kind.startswith(STOP) or kind == REMOVE:
        return False
    return name in stories.finished or name not in stories.wanted
