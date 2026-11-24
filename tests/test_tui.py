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
        self.shows = self.bells = 0

    def size(self) -> tuple[int, int]:
        return self.rows_cols

    def show(self, lines: list[str]) -> None:
        rows, cols = self.rows_cols
        assert len(lines) <= rows and all(frame.cells(SGR.sub("", line)) <= cols for line in lines)
        self.lines, self.shows = lines, self.shows + 1

    def bell(self) -> None:
        self.bells += 1

    def key(self, timeout: float) -> str | None:
        self.clock.now += timeout
        text = [SGR.sub("", line).rstrip() for line in self.lines]
        self.frames.append(text + [""] * (self.rows_cols[0] - len(text)))
        self.marked.append([SGR.sub("", line).strip() for line in self.lines if REVERSE.search(line)])
        self.keys = self.keys or ["q", "l"]
        key = self.keys.pop(0)
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


class Engine:
    def __init__(self, said: str = "", failures: list[str] | None = None) -> None:
        self.said, self.failures = said, failures or []
        self.ticks: list[int] = []
        self.refreshes = 0
        self.stopped = False
        self.term: Term | None = None

    def tick(self) -> None:
        self.ticks.append(self.term.shows if self.term else 0)

    def refresh(self) -> None:
        self.refreshes += 1

    def note(self) -> str:
        return self.said

    def stop_all(self) -> list[str]:
        self.stopped = True
        return self.failures

    def finishing(self) -> bool:
        return False


def run(
    keys: list, reader: Reader | None = None, poller: Poller | None = None, engine: Engine | None = None, **kw
) -> tuple[Term, Reader, Poller]:
    clock, reader, poller = Clock(), reader or Reader(), poller or Poller(fleet.Fleet(LIVE))
    term, engine = Term(keys, clock, **kw), engine or Engine()
    engine.term = term
    tui.Watch(term, reader, poller, clock, brief.ASCII, True, engine).run()
    return term, reader, poller


def selected(term: Term, frame: int = -1) -> list[str]:
    return term.marked[frame]


def test_q_asks_and_l_leaves_without_stopping_a_hand():
    for key in ("q", ESC):
        engine = Engine()
        term, _, _ = run([key, None, None, "l"], engine=engine)
        assert len(term.frames) == 4
        assert term.frames[0][0].startswith(" ! 2 need you | question ACME-16 3m")
        assert term.frames[0][-1].startswith(" q quit  r refresh  j/k move  enter details")
        assert [lines[-1] for lines in term.frames[1:]] == [f" {frame.ASK}"] * 3
        assert engine.stopped is False


def test_q_then_s_stops_all_hands_then_leaves():
    engine = Engine()
    term, _, _ = run(["q", "s", "j"], engine=engine)
    assert len(term.frames) == 2 and engine.stopped is True


def test_q_then_any_other_key_returns_to_the_board():
    engine = Engine()
    term, _, _ = run(["q", "j", None], engine=engine)
    assert term.frames[2][-1].startswith(" q quit  r refresh")
    assert selected(term, 2)[0].split()[1] == "ACME-14"
    assert engine.stopped is False


def test_the_lines_a_stop_all_left_come_back_from_the_run():
    clock, engine = Clock(), Engine(failures=["ACME-1: boom"])
    term = Term(["q", "s"], clock)
    assert tui.Watch(term, Reader(), Poller(fleet.Fleet(LIVE)), clock, brief.ASCII, True, engine).run() == [
        "ACME-1: boom"
    ]
    assert tui.Watch(Term(["q", "l"], clock), Reader(), Poller(), clock, brief.ASCII, True, Engine()).run() == []


def test_each_loop_ticks_the_engine_before_it_draws():
    engine = Engine()
    term, _, _ = run(["j", "k"], engine=engine)
    assert engine.ticks[0] == 1 and term.frames and len(engine.ticks) == len(term.frames)


def test_the_engine_note_shows_in_the_header():
    term, _, _ = run([], engine=Engine("cannot read Linear: network: down"), size=(30, 160))
    assert " | cannot read Linear: network: down" in term.frames[0][0]
    assert "updated" not in term.frames[0][0]
    term, _, _ = run([], Reader(None), engine=Engine("cannot read Linear: network: down"))
    assert "cannot read Linear: network: down" in term.frames[0][0]


def test_a_note_with_control_characters_draws_on_one_line():
    term, _, _ = run([], engine=Engine("ACME-1: error: one\nfatal: \x1b[2Jtwo"), size=(30, 160))
    assert "ACME-1: error: one fatal: [2Jtwo" in term.frames[0][0]
    assert all("\n" not in line and "\x1b[2J" not in line for line in term.lines)


def test_the_bell_rings_when_a_new_item_needs_you():
    one, two, other = sample(), sample(), sample()
    one["items"] = one["items"][:1]
    other["items"] = other["items"][1:]
    none = sample(items=[])
    ticks = int(tui.STATUS_EVERY / tui.TICK)
    term, _, _ = run([None] * ticks * 4, Reader(none, one, None, two, one))
    assert term.bells == 2
    term, _, _ = run([None] * ticks * 2, Reader(two))
    assert term.bells == 0
    term, _, _ = run([None] * ticks * 2, Reader(one, other))
    assert term.bells == 1


def test_stopping_says_it_finishes_an_action_in_flight():
    engine = Engine()
    engine.finishing = lambda: True
    shown = []
    engine.stop_all = lambda: shown.append(list(engine.term.lines)) or []
    run(["q", "s"], engine=engine)
    assert shown == [[tui.FINISHING]] and tui.FINISHING == " finishing an action"


def test_the_screen_says_it_reads_the_fleet_until_the_first_read():
    clock, seen = Clock(), []
    term = Term([], clock)

    class First(Poller):
        def wait(self, timeout: float) -> bool:
            seen.append(list(term.lines))
            return super().wait(timeout)

    tui.Watch(term, Reader(), First(fleet.Fleet(LIVE)), clock, brief.ASCII, True, Engine()).run()
    assert seen == [[tui.READING_FLEET]] and tui.READING_FLEET == " reading the fleet"


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
    assert tops == ["ACME-14", "ACME-16", "ACME-15", "ACME-16", "ACME-14", "ACME-14", "ACME-14"]


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


def test_r_rereads_now_and_refreshes_the_engine():
    engine = Engine()
    _, reader, _ = run(["r"], engine=engine)
    assert (reader.calls, engine.refreshes) == (2, 1)


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
    assert term.frames[1][-1].startswith(" q quit")


def test_no_snapshot_says_linear_is_being_read_and_still_quits():
    term, _, _ = run([], Reader(None))
    assert "   reading Linear for the first time" in term.frames[0]


def test_a_malformed_status_file_never_kills_the_watch():
    broken = sample()
    broken["stories"][7]["blocked_by"] = 7
    broken["items"][0]["since"] = 7
    broken["slots"] = "full"
    term, _, _ = run(["j", "j", ENTER, "j"], Reader(broken))
    assert len(term.frames) == 6


def test_a_time_out_of_range_never_kills_the_watch():
    for edge in ("9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00+14:00"):
        broken = sample()
        broken["stories"][1]["completed_at"] = edge
        broken["items"][0]["since"] = edge
        term, _, _ = run(["j"] * 8 + [ENTER], Reader(broken))
        assert len(term.frames) == 11
        term, _, _ = run([ENTER], Reader(sample(written_at=edge)))
        assert len(term.frames) == 3


def test_an_unchanged_frame_is_not_written_again():
    term, _, _ = run([None, None, None])
    assert term.shows == 3


def test_application_cursor_keys_move_too():
    term, _, _ = run(["\x1bOB", "\x1bOA"])
    assert [selected(term, n)[0].split()[1] for n in range(3)] == ["ACME-14", "ACME-16", "ACME-14"]


def test_each_row_is_cleared_before_it_is_written_and_leftover_rows_are_cleared():
    out = io.StringIO()
    screen = tui.Screen(0, out)
    screen.size = lambda: (4, 20)
    screen.show(["\x1b[2m--\x1b[0m one", "two"])
    assert out.getvalue() == "\x1b[1;1H\x1b[2K\x1b[2m--\x1b[0m one\x1b[2;1H\x1b[2Ktwo\x1b[3;1H\x1b[2K\x1b[4;1H\x1b[2K"


def test_the_bell_writes_bel_and_flushes():
    class Out(io.StringIO):
        flushed = 0

        def flush(self) -> None:
            Out.flushed += 1

    out = Out()
    tui.Screen(0, out).bell()
    assert (out.getvalue(), Out.flushed) == ("\a", 1)


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
    assert term.shows == 5


def test_the_board_fills_a_wide_terminal():
    term, _, _ = run([], size=(30, 160))
    rule = next(line for line in term.frames[0] if line.startswith("-- needs you"))
    assert len(rule) == 160
