from __future__ import annotations

import io
import re
import threading

from deckhand import brief, fleet, frame, tui
from tests.test_board import LIVE, NOW, sample

ENTER, ESC, DOWN, UP = "\r", "\x1b", "\x1b[B", "\x1b[A"
SGR = re.compile(r"\x1b\[[0-9;]*m")
REVERSE = re.compile(r"\x1b\[(?:[0-9;]*;)?7m")


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class Term:
    def __init__(self, keys: list, clock: Clock, size: tuple[int, int] = (30, 100)) -> None:
        self.keys, self.clock, self.rows_cols = list(keys), clock, size
        self.lines: list[str] = []
        self.frames: list[list[str]] = []
        self.marked: list[list[str]] = []
        self.shows = 0

    def size(self) -> tuple[int, int]:
        return self.rows_cols

    def show(self, lines: list[str]) -> None:
        rows, cols = self.rows_cols
        assert len(lines) <= rows and all(frame.cells(SGR.sub("", line)) <= cols for line in lines)
        self.lines, self.shows = lines, self.shows + 1

    def key(self, timeout: float) -> str | None:
        self.clock.now += timeout
        text = [SGR.sub("", line).rstrip() for line in self.lines]
        self.frames.append(text + [""] * (self.rows_cols[0] - len(text)))
        self.marked.append([SGR.sub("", line).strip() for line in self.lines if REVERSE.search(line)])
        key = self.keys.pop(0) if self.keys else "q"
        if isinstance(key, tuple):
            self.rows_cols = key
            return None
        return key


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


def run(
    keys: list, reader: Reader | None = None, poller: Poller | None = None, refresh=None, **kw
) -> tuple[Term, Reader, Poller]:
    clock, reader, poller = Clock(), reader or Reader(), poller or Poller(fleet.Fleet(LIVE))
    term = Term(keys, clock, **kw)
    tui.Watch(term, reader, poller, clock, brief.ASCII, True, refresh).run()
    return term, reader, poller


def selected(term: Term, frame: int = -1) -> list[str]:
    return term.marked[frame]


def test_q_and_esc_quit_after_one_frame():
    for key in ("q", ESC):
        term, _, _ = run([key])
        assert len(term.frames) == 1
        assert term.frames[0][0].startswith(" ! 2 need you | question ACME-16 3m")
        assert term.frames[0][-1].startswith(" q quit  r refresh  j/k move  enter details")


def test_the_first_frame_waits_for_the_first_fleet_read():
    term, _, poller = run([])
    assert poller.waited == [tui.FIRST_READ]
    assert "slots 2/2" in term.frames[0][0]
    assert any("building, waiting" in line for line in term.frames[0])


def test_a_first_read_that_never_lands_still_draws_from_the_file():
    term, _, _ = run([], poller=Poller(None, first=False))
    assert term.frames[0][0].startswith(" ! 2 need you")


def test_the_first_row_starts_selected_and_j_k_and_arrows_move():
    term, _, _ = run(["j", DOWN, "k", UP, UP])
    tops = [selected(term, n)[0].split()[1] for n in range(len(term.frames))]
    assert tops == ["ACME-14", "ACME-16", "ACME-15", "ACME-16", "ACME-14", "ACME-14"]


def test_enter_opens_and_closes_the_detail_line():
    term, _, _ = run(["j", "j", ENTER, ENTER])
    opened = [line.split() for line in term.frames[3]]
    assert ["hand:", "claude", "attach", "a1b2"] in opened
    assert ["hand:", "claude", "attach", "a1b2"] not in [line.split() for line in term.frames[4]]


def test_the_selection_follows_its_row_when_the_file_changes():
    later = sample()
    later["items"] = later["items"][1:]
    term, _, _ = run(["j", "j", None] + [None] * 10, Reader(sample(), later))
    assert selected(term, 2)[0].split()[1] == "ACME-15"
    assert selected(term)[0].split()[1] == "ACME-15"


def test_the_file_is_reread_every_two_seconds():
    ticks = int(2 / tui.TICK)
    _, reader, _ = run([None] * (ticks * 2))
    assert reader.calls == 3


def test_r_rereads_now_and_pokes_the_fleet():
    _, reader, poller = run(["r"])
    assert (reader.calls, poller.pokes) == (2, 1)


def test_without_a_live_read_the_file_hands_show():
    term, _, _ = run(["j", "j", ENTER], poller=Poller(None))
    assert not any("claude attach" in line for line in term.frames[-1])
    assert any("ACME-15" in line for line in term.frames[-1])


def test_resize_redraws_at_the_new_size():
    term, _, _ = run([(40, 59), (6, 80)])
    assert len(term.frames[1]) == 40
    assert "Print every greeting" not in "\n".join(term.frames[1])
    assert term.frames[2][0].startswith(" ! 2 need you | question ACME-16 3m | updated 6s ago | Alpha")
    assert term.frames[2][1:] == [""] * 5


def test_drawing_into_the_last_cell_never_raises():
    term, _, _ = run([(10, 40)])
    assert term.frames[-1][-1].startswith(" q quit")


def test_no_status_file_teaches_and_still_quits():
    term, _, _ = run([], Reader(None))
    assert "   no captain has run yet; start one with /deckhand:captain" in term.frames[0]


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
    term, _, _ = run(["j", "j", ENTER, "j"], Reader(broken))
    assert len(term.frames) == 5


def test_a_time_out_of_range_never_kills_the_watch():
    for edge in ("9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00+14:00"):
        broken = sample()
        broken["stories"][1]["completed_at"] = edge
        broken["items"][0]["since"] = edge
        term, _, _ = run(["j"] * 8 + [ENTER], Reader(broken))
        assert len(term.frames) == 10
        term, _, _ = run([ENTER], Reader(sample(written_at=edge)))
        assert len(term.frames) == 2


def test_r_runs_a_full_read_shows_refreshing_and_rereads_when_it_ends():
    started, left = [], [3]

    def refresh():
        started.append(1)

        def running() -> bool:
            left[0] -= 1
            return left[0] > 0

        return running

    term, reader, _ = run(["r", "r", None, None, None], refresh=refresh)
    assert started == [1]
    assert "refreshing" in term.frames[1][0] and "updated" not in term.frames[1][0]
    assert "refreshing" not in term.frames[-1][0] and "updated" in term.frames[-1][0]
    assert reader.calls == 4


def test_an_unchanged_frame_is_not_written_again():
    term, _, _ = run([None, None, None])
    assert term.shows == 1


def test_application_cursor_keys_move_too():
    term, _, _ = run(["\x1bOB", "\x1bOA"])
    assert [selected(term, n)[0].split()[1] for n in range(3)] == ["ACME-14", "ACME-16", "ACME-14"]


def test_each_row_is_cleared_before_it_is_written_and_leftover_rows_are_cleared():
    out = io.StringIO()
    screen = tui.Screen(0, out)
    screen.size = lambda: (4, 20)
    screen.show(["\x1b[2m--\x1b[0m one", "two"])
    assert out.getvalue() == "\x1b[1;1H\x1b[2K\x1b[2m--\x1b[0m one\x1b[2;1H\x1b[2Ktwo\x1b[3;1H\x1b[2K\x1b[4;1H\x1b[2K"


def test_keys_read_together_come_out_one_at_a_time():
    assert tui.split_key("jj") == ("j", "j")
    assert tui.split_key("\x1b[B\x1b[B") == ("\x1b[B", "\x1b[B")
    assert tui.split_key("\x1bOAk") == ("\x1bOA", "k")
    assert tui.split_key("\x1b[1;5B") == ("\x1b[1;5B", "")
    assert tui.split_key("\x1bq") == ("\x1b", "q")


def test_an_arrow_split_across_reads_is_not_a_quit():
    import os

    read, write = os.pipe()
    screen = tui.Screen(read, io.StringIO())
    os.write(write, b"\x1b")
    threading.Timer(0.005, lambda: os.write(write, b"[B")).start()
    tui.ESC_WAIT, wait = 0.5, tui.ESC_WAIT
    try:
        assert screen.key(1) == "\x1b[B"
        os.write(write, b"jj")
        assert [screen.key(1), screen.key(0)] == ["j", "j"]
        os.write(write, b"\x1b")
        tui.ESC_WAIT = 0.01
        assert screen.key(1) == "\x1b"
    finally:
        tui.ESC_WAIT = wait
        os.close(read)
        os.close(write)


def test_a_resize_that_keeps_the_lines_still_redraws():
    term, _, _ = run([(30, 140), (40, 140)], size=(30, 120))
    assert term.shows == 3
