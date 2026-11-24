from __future__ import annotations

import threading
from collections.abc import Callable

from deckhand import fleet, tui
from deckhand.poller import Poller
from tests.test_board import LIVE


def poller_of(reader: Callable[[], fleet.Fleet]) -> Poller[fleet.Fleet]:
    return Poller(reader, 60, tui.fleet_failed, "deckhand-fleet")


def test_the_poller_reads_off_the_caller_thread_and_a_poke_reads_again():
    seen, read = [], threading.Event()

    def reader() -> fleet.Fleet:
        seen.append(threading.current_thread())
        read.set()
        return fleet.Fleet(LIVE[:1])

    poller = poller_of(reader)
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

    poller = poller_of(reader)
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

    poller = poller_of(reader)
    poller.start()
    assert inside.wait(5)
    poller.poke()
    gate.set()
    assert again.wait(5)
    poller.stop()


def test_the_real_poller_says_when_the_first_read_landed():
    gate = threading.Event()
    poller = poller_of(lambda: gate.wait(5) and fleet.Fleet([]))
    poller.start()
    assert not poller.wait(0.05)
    gate.set()
    assert poller.wait(5)
    poller.stop()


def test_a_landed_read_says_when_it_began():
    times = iter([10.0, 20.0])

    def reader() -> fleet.Fleet:
        return fleet.Fleet([])

    poller = Poller(reader, 60, tui.fleet_failed, "deckhand-fleet", clock=lambda: next(times))
    assert poller.began() is None
    poller.start()
    assert poller.wait(5)
    poller.stop()
    assert poller.began() == 10.0
