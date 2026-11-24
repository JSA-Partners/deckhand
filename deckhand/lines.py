from __future__ import annotations

from collections.abc import Set

from deckhand import fleet
from deckhand.reconcile import RETRY_AFTER, RETRY_CAP, Action, Launch, Remove, Resume, Stop

START = "start"


def _key(action: Action) -> tuple[str, str, str]:
    if isinstance(action, Launch | Resume):
        return START, action.name, ""
    if isinstance(action, Stop | Remove):
        return type(action).__name__, action.name, action.short
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
        return {name: tries for (kind, name, _), tries in self._tries.items() if kind == START}

    def backing_off(self, action: Action, now: float) -> bool:
        if not isinstance(action, Stop | Remove):
            return False
        tries, last = self._tries.get(_key(action), (0, now))
        return bool(tries) and now - last < min(RETRY_AFTER * 2 ** (tries - 1), RETRY_CAP)

    # Only a good fleet read that shows the cause gone, or a finished story, clears a failure.
    def settle(self, read: fleet.Fleet, finished: Set[str]) -> None:
        if read.note:
            return
        alive = {s.name for s in read.sessions if fleet.alive(s)}
        running = {s.id for s in read.sessions if fleet.alive(s)}
        listed = {s.id for s in read.sessions}
        for key in list(self._tries.keys() | self._held.keys()):
            kind, name, short = key
            gone = {START: name in alive, "Stop": short not in running, "Remove": short not in listed}
            ended = name in finished and kind not in ("Stop", "Remove")
            if name and (ended or gone.get(kind, False)):
                self._forget(key)

    def newest(self) -> str:
        return next(reversed(self._held.values()))[0] if self._held else ""
