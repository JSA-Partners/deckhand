from __future__ import annotations

import curses
import locale
import threading
from collections.abc import Callable

from deckhand import board, fleet, frame
from deckhand.brief import Glyphs

TICK_MS = 250
STATUS_EVERY = 2.0
FLEET_EVERY = 5.0
FIRST_READ = 1.0
QUIT = (ord("q"), 27)
DOWN = (ord("j"), curses.KEY_DOWN)
UP = (ord("k"), curses.KEY_UP)
ENTER = (10, 13, curses.KEY_ENTER)
PALETTE = (
    ("needs", curses.COLOR_RED, curses.A_BOLD),
    ("working", curses.COLOR_GREEN, 0),
    ("warn", curses.COLOR_YELLOW, curses.A_BOLD),
    ("command", curses.COLOR_CYAN, 0),
)


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
        screen,
        read_status: Callable[[], dict | None],
        poller: Poller,
        clock: Callable[[], float],
        g: Glyphs,
        styles: dict[str, int],
    ) -> None:
        self._screen, self._read, self._poller, self._clock, self._g = screen, read_status, poller, clock, g
        self._styles = styles
        self._status: dict | None = None
        self._read_at = float("-inf")
        self._keys: list[str] = []
        self.selected: str | None = None
        self.expanded: str | None = None

    def _reread(self) -> None:
        self._status, self._read_at = self._read(), self._clock()

    def _board(self) -> board.Board:
        now = self._clock()
        return board.build(self._status, self._poller.latest(), now, board.midnight(now), self._g)

    def _follow(self, keys: list[str]) -> None:
        if self.selected not in keys:
            at = self._keys.index(self.selected) if self.selected in self._keys else 0
            self.selected = keys[min(at, len(keys) - 1)] if keys else None
        self._keys = keys

    def _move(self, step: int) -> None:
        if self._keys and self.selected in self._keys:
            at = self._keys.index(self.selected) + step
            self.selected = self._keys[max(0, min(at, len(self._keys) - 1))]

    def _put(self, y: int, x: int, text: str, attr: int) -> None:
        try:
            self._screen.addstr(y, x, text, attr)
        except curses.error:
            return

    def draw(self) -> None:
        found = self._board()
        self._follow(found.keys())
        rows, cols = self._screen.getmaxyx()
        lines = frame.render(found, self._g, cols, rows, self.selected, self.expanded, keys=True)
        self._screen.erase()
        for y, line in enumerate(lines[:rows]):
            x, reverse = 0, self._styles.get("selected", curses.A_REVERSE) if line.selected else 0
            for text, style in line.spans:
                self._put(y, x, text, self._styles.get(style, 0) | reverse)
                x += frame.cells(text)
        self._screen.refresh()

    def handle(self, key: int) -> bool:
        if key in QUIT:
            return False
        if key == ord("r"):
            self._reread()
            self._poller.poke()
        elif key in DOWN or key in UP:
            self._move(1 if key in DOWN else -1)
        elif key in ENTER and self.selected:
            self.expanded = None if self.expanded == self.selected else self.selected
        return True

    def run(self) -> None:
        self._poller.wait(FIRST_READ)
        while True:
            if self._clock() - self._read_at >= STATUS_EVERY:
                self._reread()
            self.draw()
            if not self.handle(self._screen.getch()):
                return


def _setup(screen, color: bool) -> dict[str, int]:
    for call in (lambda: curses.curs_set(0), lambda: curses.set_escdelay(25)):
        try:
            call()
        except curses.error:
            pass
    screen.timeout(TICK_MS)
    styles = {"bold": curses.A_BOLD, "dim": curses.A_DIM, "done": curses.A_DIM, "stale": curses.A_DIM}
    styles |= {"stopped": curses.A_BOLD}
    styles |= {"needs": curses.A_BOLD, "warn": curses.A_BOLD, "selected": curses.A_REVERSE}
    if color and curses.has_colors():
        curses.use_default_colors()
        for number, (style, foreground, extra) in enumerate(PALETTE, 1):
            curses.init_pair(number, foreground, -1)
            styles[style] = curses.color_pair(number) | extra
    return styles


def watch(
    read_status: Callable[[], dict | None],
    read_fleet: Callable[[], fleet.Fleet],
    clock: Callable[[], float],
    color: bool,
    g: Glyphs,
) -> None:
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    poller = Poller(read_fleet)
    poller.start()
    try:
        curses.wrapper(lambda screen: Watch(screen, read_status, poller, clock, g, _setup(screen, color)).run())
    except KeyboardInterrupt:
        return
    finally:
        poller.stop()
