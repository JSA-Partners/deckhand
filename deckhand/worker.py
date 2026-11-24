from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from typing import Protocol

from deckhand import fleet, git
from deckhand.acts import Acts
from deckhand.linear.client import LinearError
from deckhand.reconcile import Action
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
        yield Acts(state, LocalRecords(path), lambda: stores("person"), runner)


class Worker:
    def __init__(self, opened: Callable[[], AbstractContextManager[Runs]] = opened_acts) -> None:
        self._opened = opened
        self._queue: queue.SimpleQueue[Action | None] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._busy: dict[str, int] = {}
        self._done: list[tuple[Action, str | None]] = []
        self._thread = threading.Thread(target=self._loop, name="deckhand-worker", daemon=True)

    def _loop(self) -> None:
        with ExitStack() as stack:
            try:
                run = stack.enter_context(self._opened()).run
            except Exception as error:
                failure = problem(error)

                def run(action: Action) -> str | None:
                    return f"{fleet.ref_of(action.name)}: {failure}"

            while (action := self._queue.get()) is not None:
                line = run(action)
                with self._lock:
                    self._busy[action.name] -= 1
                    if not self._busy[action.name]:
                        del self._busy[action.name]
                    self._done.append((action, line))

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout)

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
