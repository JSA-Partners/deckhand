from __future__ import annotations

import curses
import threading

from deckhand import brief, fleet, frame, tui
from tests.test_board import LIVE, NOW, sample

ENTER, ESC = 10, 27


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class Screen:
    def __init__(self, keys: list, clock: Clock, size: tuple[int, int] = (30, 100)) -> None:
        self.keys, self.clock, self.size = list(keys), clock, size
        self.cells: dict[int, list[tuple[int, str, int]]] = {}
        self.frames: list[list[str]] = []
        self.attrs: list[dict[int, list[tuple[str, int]]]] = []

    def getch(self) -> int:
        self.clock.now += tui.TICK_MS / 1000
        key = self.keys.pop(0) if self.keys else ord("q")
        if isinstance(key, tuple):
            key, self.size = curses.KEY_RESIZE, key
        return ord(key) if isinstance(key, str) else key

    def getmaxyx(self) -> tuple[int, int]:
        return self.size

    def erase(self) -> None:
        self.cells = {}

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        rows, cols = self.size
        end = x + frame.cells(text)
        if y >= rows or end > cols or (y == rows - 1 and end == cols):
            raise curses.error("addstr() returned ERR")
        self.cells.setdefault(y, []).append((x, text, attr))

    def refresh(self) -> None:
        frame = []
        for y in range(self.size[0]):
            spans = sorted(self.cells.get(y, []))
            frame.append("".join(text for _, text, _ in spans).rstrip())
        self.frames.append(frame)
        self.attrs.append({y: [(text, attr) for _, text, attr in sorted(spans)] for y, spans in self.cells.items()})


class Poller:
    def __init__(self, read: fleet.Fleet | None = None, first: bool = True) -> None:
        self.read, self.pokes, self.waited, self.first = read, 0, [], first

    def wait(self, timeout: float) -> bool:
        self.waited.append(timeout)
        return self.first

    def latest(self) -> fleet.Fleet | None:
        return self.read if self.waited else None

    def poke(self) -> None:
        self.pokes += 1


class Reader:
    def __init__(self, *found: dict | None) -> None:
        self.found, self.calls = list(found) or [sample()], 0

    def __call__(self) -> dict | None:
        self.calls += 1
        return self.found[min(self.calls, len(self.found)) - 1]


def run(keys: list, reader: Reader | None = None, poller: Poller | None = None, **kw) -> tuple[Screen, Reader, Poller]:
    clock, reader, poller = Clock(), reader or Reader(), poller or Poller(fleet.Fleet(LIVE))
    screen = Screen(keys, clock, **kw)
    tui.Watch(screen, reader, poller, clock, brief.ASCII, {"selected": 1 << 20}).run()
    return screen, reader, poller


def selected(screen: Screen, frame: int = -1) -> list[str]:
    rows = screen.attrs[frame].values()
    return ["".join(text for text, _ in spans).strip() for spans in rows if any(a & 1 << 20 for _, a in spans)]


def test_q_and_esc_quit_after_one_frame():
    for key in ("q", ESC):
        screen, _, _ = run([key])
        assert len(screen.frames) == 1
        assert screen.frames[0][0].startswith(" ! 2 need you | question ACME-16 3m")
        assert screen.frames[0][-1].startswith(" q quit  r refresh  j/k move  enter details")


def test_the_first_frame_waits_for_the_first_fleet_read():
    screen, _, poller = run([])
    assert poller.waited == [tui.FIRST_READ]
    assert "slots 2/2" in screen.frames[0][0]
    assert any("building, waiting" in line for line in screen.frames[0])


def test_a_first_read_that_never_lands_still_draws_from_the_file():
    screen, _, _ = run([], poller=Poller(None, first=False))
    assert screen.frames[0][0].startswith(" ! 2 need you")


def test_the_first_row_starts_selected_and_j_k_and_arrows_move():
    screen, _, _ = run(["j", curses.KEY_DOWN, "k", curses.KEY_UP, curses.KEY_UP])
    tops = [selected(screen, n)[0].split()[1] for n in range(len(screen.frames))]
    assert tops == ["ACME-14", "ACME-16", "ACME-15", "ACME-16", "ACME-14", "ACME-14"]


def test_enter_opens_and_closes_the_detail_line():
    screen, _, _ = run(["j", "j", ENTER, ENTER])
    opened = [line.split() for line in screen.frames[3]]
    assert ["hand:", "claude", "attach", "a1b2"] in opened
    assert ["hand:", "claude", "attach", "a1b2"] not in [line.split() for line in screen.frames[4]]


def test_the_selection_follows_its_row_when_the_file_changes():
    later = sample()
    later["items"] = later["items"][1:]
    screen, _, _ = run(["j", "j", -1] + [-1] * 10, Reader(sample(), later))
    assert selected(screen, 2)[0].split()[1] == "ACME-15"
    assert selected(screen)[0].split()[1] == "ACME-15"


def test_the_file_is_reread_every_two_seconds():
    ticks = int(2 / (tui.TICK_MS / 1000))
    _, reader, _ = run([-1] * (ticks * 2))
    assert reader.calls == 3


def test_r_rereads_now_and_pokes_the_fleet():
    _, reader, poller = run(["r"])
    assert (reader.calls, poller.pokes) == (2, 1)


def test_without_a_live_read_the_file_hands_show():
    screen, _, _ = run(["j", "j", ENTER], poller=Poller(None))
    assert not any("claude attach" in line for line in screen.frames[-1])
    assert any("ACME-15" in line for line in screen.frames[-1])


def test_resize_redraws_at_the_new_size():
    screen, _, _ = run([(40, 59), (6, 80)])
    assert len(screen.frames[1]) == 40
    assert "Print every greeting" not in "\n".join(screen.frames[1])
    assert screen.frames[2][0].startswith(" ! 2 need you | question ACME-16 3m | updated 6s ago | Alpha")
    assert screen.frames[2][1:] == [""] * 5


def test_drawing_into_the_last_cell_never_raises():
    screen, _, _ = run([(10, 40)])
    assert screen.frames[-1][-1].startswith(" q quit")


def test_no_status_file_teaches_and_still_quits():
    screen, _, _ = run([], Reader(None))
    assert "   no captain has run yet; start one with /deckhand:captain" in screen.frames[0]


def test_the_poller_reads_off_the_caller_thread_and_a_poke_reads_again():
    seen, read = [], threading.Event()

    def reader() -> fleet.Fleet:
        seen.append(threading.current_thread())
        read.set()
        return fleet.Fleet(LIVE[:1])

    poller = tui.Poller(reader, every=60)
    assert poller.latest() is None
    poller.start()
    assert read.wait(5)
    read.clear()
    poller.poke()
    assert read.wait(5)
    poller.stop()
    assert len(seen) >= 2 and threading.current_thread() not in seen
    assert poller.latest() == fleet.Fleet(LIVE[:1])


def test_a_failing_fleet_read_keeps_the_poller_alive():
    calls, read = [], threading.Event()

    def reader() -> fleet.Fleet:
        calls.append(1)
        read.set()
        if len(calls) == 1:
            raise OSError("claude vanished")
        return fleet.Fleet(LIVE[:1])

    poller = tui.Poller(reader, every=60)
    poller.start()
    assert read.wait(5)
    read.clear()
    poller.poke()
    assert read.wait(5)
    poller.stop()
    assert len(calls) >= 2


def test_a_poke_during_a_read_is_never_dropped():
    calls, inside, gate, again = [], threading.Event(), threading.Event(), threading.Event()

    def reader() -> fleet.Fleet:
        calls.append(1)
        if len(calls) == 1:
            inside.set()
            assert gate.wait(5)
        else:
            again.set()
        return fleet.Fleet([])

    poller = tui.Poller(reader, every=60)
    poller.start()
    assert inside.wait(5)
    poller.poke()
    gate.set()
    assert again.wait(5)
    poller.stop()


def test_the_real_poller_says_when_the_first_read_landed():
    gate = threading.Event()
    poller = tui.Poller(lambda: gate.wait(5) and fleet.Fleet([]), every=60)
    poller.start()
    assert not poller.wait(0.05)
    gate.set()
    assert poller.wait(5)
    poller.stop()


def test_a_malformed_status_file_never_kills_the_watch():
    broken = sample()
    broken["stories"][7]["blocked_by"] = 7
    broken["items"][0]["since"] = 7
    broken["slots"] = "full"
    screen, _, _ = run(["j", "j", ENTER, "j"], Reader(broken))
    assert len(screen.frames) == 5
