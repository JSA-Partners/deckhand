from __future__ import annotations

import os
import select
import sys
import termios
import threading
import tty
from collections.abc import Callable
from typing import Protocol

from deckhand import board, fleet, frame
from deckhand.brief import Glyphs

TICK = 0.25
STATUS_EVERY = 2.0
FLEET_EVERY = 5.0
FIRST_READ = 1.0
KEYS = {"q": "quit", "\x1b": "quit", "j": "down", "k": "up", "r": "refresh", "\r": "enter", "\n": "enter"}
KEYS |= {"\x1b[A": "up", "\x1b[B": "down", "\x1bOA": "up", "\x1bOB": "down"}
ENTER_SCREEN, LEAVE_SCREEN = "\x1b[?1049h\x1b[?25l", "\x1b[?25h\x1b[?1049l"

Refresh = Callable[[], Callable[[], bool]]


class Terminal(Protocol):
    def size(self) -> tuple[int, int]: ...

    def key(self, timeout: float) -> str | None: ...

    def show(self, lines: list[str]) -> None: ...


class Poller:
    def __init__(self, read: Callable[[], fleet.Fleet], every: float = FLEET_EVERY) -> None:
        self._read, self._every = read, every
        self._lock, self._wake, self._stop, self._first = (
            threading.Lock(),
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self._latest: fleet.Fleet | None = None
        self._thread = threading.Thread(target=self._loop, name="deckhand-fleet", daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                found = self._read()
            except Exception as error:
                found = fleet.Fleet([], f"the fleet read failed: {type(error).__name__}")
            with self._lock:
                self._latest = found
            self._first.set()
            self._wake.wait(self._every)

    def start(self) -> None:
        self._thread.start()

    def wait(self, timeout: float) -> bool:
        return self._first.wait(timeout)

    def latest(self) -> fleet.Fleet | None:
        with self._lock:
            return self._latest

    def poke(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()


class Watch:
    def __init__(
        self,
        term: Terminal,
        read_status: Callable[[], dict | None],
        poller: Poller,
        clock: Callable[[], float],
        g: Glyphs,
        color: bool,
        refresh: Refresh | None = None,
    ) -> None:
        self._term, self._read, self._poller, self._clock, self._g = term, read_status, poller, clock, g
        self._color, self._refresh = color, refresh
        self._running: Callable[[], bool] | None = None
        self._status: dict | None = None
        self._read_at = float("-inf")
        self._keys: list[str] = []
        self._shown: list[str] | None = None
        self.selected: str | None = None
        self.expanded: str | None = None

    def _reread(self) -> None:
        self._status, self._read_at = self._read(), self._clock()

    def _follow(self, keys: list[str]) -> None:
        if self.selected not in keys:
            at = self._keys.index(self.selected) if self.selected in self._keys else 0
            self.selected = keys[min(at, len(keys) - 1)] if keys else None
        self._keys = keys

    def _move(self, step: int) -> None:
        if self._keys and self.selected in self._keys:
            at = self._keys.index(self.selected) + step
            self.selected = self._keys[max(0, min(at, len(self._keys) - 1))]

    def _refreshing(self) -> bool:
        if self._running is not None and not self._running():
            self._running = None
            self._reread()
        return self._running is not None

    def draw(self) -> None:
        now = self._clock()
        found = board.build(self._status, self._poller.latest(), now, board.midnight(now), self._g)
        self._follow(found.keys())
        rows, cols = self._term.size()
        lines = frame.render(
            found, self._g, cols, rows, self.selected, self.expanded, keys=True, refreshing=self._refreshing()
        )
        painted = [frame.paint(line, self._color) for line in lines[:rows]]
        if painted != self._shown:
            self._term.show(painted)
            self._shown = painted

    def handle(self, key: str | None) -> bool:
        action = KEYS.get(key or "")
        if action == "quit":
            return False
        if action == "refresh":
            self._reread()
            self._poller.poke()
            if self._refresh is not None and self._running is None:
                self._running = self._refresh()
        elif action in ("down", "up"):
            self._move(1 if action == "down" else -1)
        elif action == "enter" and self.selected:
            self.expanded = None if self.expanded == self.selected else self.selected
        return True

    def run(self) -> None:
        self._poller.wait(FIRST_READ)
        while True:
            if self._clock() - self._read_at >= STATUS_EVERY:
                self._reread()
            self.draw()
            if not self.handle(self._term.key(TICK)):
                return


# Plain escape codes, row by row: a curses build may repeat a non-ASCII character in a way terminals misdraw.
class Screen:
    def __init__(self, fd: int, out) -> None:
        self._fd, self._out = fd, out
        self._saved: list | None = None

    def __enter__(self) -> Screen:
        self._saved = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        self._out.write(ENTER_SCREEN)
        self._out.flush()
        return self

    def __exit__(self, *_: object) -> None:
        self._out.write(LEAVE_SCREEN)
        self._out.flush()
        if self._saved is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def size(self) -> tuple[int, int]:
        found = os.get_terminal_size(self._out.fileno())
        return found.lines, found.columns

    def key(self, timeout: float) -> str | None:
        ready, _, _ = select.select([self._fd], [], [], timeout)
        if not ready:
            return None
        return os.read(self._fd, 32).decode("utf-8", "replace")

    def show(self, lines: list[str]) -> None:
        rows, _ = self.size()
        drawn = [f"\x1b[{y + 1};1H\x1b[2K{line}" for y, line in enumerate(lines)]
        drawn += [f"\x1b[{y + 1};1H\x1b[2K" for y in range(len(lines), rows)]
        self._out.write("".join(drawn))
        self._out.flush()


def watch(
    read_status: Callable[[], dict | None],
    read_fleet: Callable[[], fleet.Fleet],
    clock: Callable[[], float],
    color: bool,
    g: Glyphs,
    refresh: Refresh | None = None,
) -> None:
    poller = Poller(read_fleet)
    poller.start()
    try:
        with Screen(sys.stdin.fileno(), sys.stdout) as screen:
            Watch(screen, read_status, poller, clock, g, color, refresh).run()
    except KeyboardInterrupt:
        return
    finally:
        poller.stop()
