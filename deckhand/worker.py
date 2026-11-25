from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from typing import Protocol

from deckhand import fleet, git
from deckhand.actions import Action
from deckhand.acts import Acts
from deckhand.linear.client import LinearError
from deckhand.step import Refusal
from deckhand.store.local import LocalRecords, default_path
from deckhand.store.open import Stores
from deckhand.store.state import LocalState


def problem(error: Exception) -> str:
    if isinstance(error, LinearError):
        return f"{error.code}: {error.message}"
    return error.rule if isinstance(error, Refusal) else type(error).__name__


class Runs(Protocol):
    def run(self, action: Action) -> str | None: ...


@contextmanager
def opened_acts(runner: git.Runner = git.run) -> Iterator[Runs]:
    path = default_path()
    with LocalState(path) as state:
        stores = Stores()
        yield Acts(state, LocalRecords(path), lambda: stores("person"), runner, lambda: stores("app"))


class Worker:
    def __init__(self, opened: Callable[[], AbstractContextManager[Runs]] = opened_acts) -> None:
        self._opened = opened
        self._queue: queue.SimpleQueue[Action | None] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self._busy: dict[str, int] = {}
        self._current: Action | None = None
        self._done: list[tuple[Action, str | None]] = []
        self._thread = threading.Thread(target=self._loop, name="deckhand-worker", daemon=True)

    def _loop(self) -> None:
        with ExitStack() as stack:
            acts: Runs | None = None
            while (action := self._queue.get()) is not None and not self._halt.is_set():
                if acts is None:
                    try:
                        acts = stack.enter_context(self._opened())
                    except Exception as error:
                        self._done_with(action, f"{fleet.ref_of(action.name)}: {problem(error)}")
                        continue
                self._current = action
                try:
                    line = acts.run(action)
                finally:
                    self._current = None
                self._done_with(action, line)

    def _done_with(self, action: Action, line: str | None) -> None:
        with self._lock:
            self._busy[action.name] -= 1
            if not self._busy[action.name]:
                del self._busy[action.name]
            self._done.append((action, line))

    def start(self) -> None:
        self._thread.start()

    # No bound of its own: an action in flight ends within its commands' timeouts, and a launch must land first.
    def stop(self, timeout: float | None = None) -> None:
        self._halt.set()
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout)

    def working(self) -> bool:
        return self._thread.is_alive() and self._current is not None

    def submit(self, action: Action) -> None:
        with self._lock:
            self._busy[action.name] = self._busy.get(action.name, 0) + 1
        self._queue.put(action)

    def busy(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._busy)

    def results(self) -> list[tuple[Action, str | None]]:
        with self._lock:
            found, self._done = self._done, []
        return found
