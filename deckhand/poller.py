from __future__ import annotations

import threading
import time
from collections.abc import Callable


class Poller[T]:
    def __init__(
        self,
        read: Callable[[], T],
        every: float,
        failed: Callable[[Exception], T],
        name: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._read, self._every, self._failed, self._clock = read, every, failed, clock
        self._lock, self._wake, self._stop, self._first = (
            threading.Lock(),
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self._latest: T | None = None
        self._began: float | None = None
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            began = self._clock()
            try:
                found = self._read()
            except Exception as error:
                found = self._failed(error)
            with self._lock:
                self._latest, self._began = found, began
            self._first.set()
            self._wake.wait(self._every)

    def start(self) -> None:
        self._thread.start()

    def wait(self, timeout: float) -> bool:
        return self._first.wait(timeout)

    def latest(self) -> T | None:
        with self._lock:
            return self._latest

    def began(self) -> float | None:
        with self._lock:
            return self._began

    def poke(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
