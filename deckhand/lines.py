from __future__ import annotations

from collections.abc import Set

from deckhand.reconcile import Action, Launch, Resume


class Lines:
    def __init__(self) -> None:
        self._held: dict[str, tuple[str | None, str, float]] = {}
        self._failed: dict[str, tuple[int, float]] = {}

    def say(self, key: str, line: str, at: float = 0.0, name: str | None = None) -> None:
        self._held.pop(key, None)
        self._held[key] = (name, line, at)

    def clear(self, key: str) -> None:
        self._held.pop(key, None)

    def heard(self, action: Action, line: str | None, now: float) -> None:
        if isinstance(action, (Launch, Resume)):
            if line is None:
                self._failed.pop(action.name, None)
            else:
                self._failed[action.name] = (self._failed.get(action.name, (0, now))[0] + 1, now)
        key = f"{type(action).__name__}:{action.name}"
        if line is None:
            self.clear(key)
        else:
            self.say(key, line, now, action.name)

    def named(self) -> bool:
        return any(name is not None for name, _, _ in self._held.values())

    # A fleet read newer than a line that leaves reconcile nothing to do for its name means the cause is gone.
    def settle(self, wanted: Set[str], began: float) -> None:
        for key, (name, _, at) in list(self._held.items()):
            if name is not None and name not in wanted and began > at:
                del self._held[key]
                self._failed.pop(name, None)

    def failed(self) -> dict[str, tuple[int, float]]:
        return dict(self._failed)

    def newest(self) -> str:
        return next(reversed(self._held.values()))[1] if self._held else ""
