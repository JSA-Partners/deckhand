from __future__ import annotations

from deckhand import watch
from deckhand.store.state import SLEEP_GAP
from deckhand.watch import MAIL, QUEUE, RUNNING, STOPPED, Seen

T = 1795000000.0


def test_a_new_top_item_rings_once():
    seen, lines = watch.captain(Seen("", True, 0, T), "k1", False, T, T + 1)
    assert lines == [QUEUE]
    seen, lines = watch.captain(seen, "k1", False, T + 1, T + 2)
    assert lines == []
    _, lines = watch.captain(seen, "k2", False, T + 2, T + 3)
    assert lines == [QUEUE]


def test_an_unchanged_top_item_never_rings_again():
    seen = Seen("k1", True, 0, T)
    for tick in range(1, 601):
        seen, lines = watch.captain(seen, "k1", False, T + tick, T + tick)
        assert lines == []


def test_a_held_queue_is_silent_and_rings_on_release():
    seen, lines = watch.captain(Seen("", True, 0, T), "k1", True, T, T + 1)
    assert lines == []
    seen, lines = watch.captain(seen, "k1", True, T + 1, T + 2)
    assert lines == []
    seen, lines = watch.captain(seen, "k1", False, T + 2, T + 3)
    assert lines == [QUEUE]
    _, lines = watch.captain(seen, "k1", False, T + 3, T + 4)
    assert lines == []


def test_an_emptied_queue_rings_again_for_the_same_item():
    seen, _ = watch.captain(Seen("k1", True, 0, T), "", False, T, T + 1)
    _, lines = watch.captain(seen, "k1", False, T + 1, T + 2)
    assert lines == [QUEUE]


def test_a_stale_heartbeat_says_stopped_once_and_running_when_it_returns():
    seen = Seen("", True, 0, T)
    seen, lines = watch.captain(seen, "", False, T, T + 31)
    assert lines == []
    seen = Seen(seen.top, seen.engine, seen.seq, T + 31)
    seen, lines = watch.captain(seen, "", False, T, T + 32)
    assert lines == [STOPPED]
    seen, lines = watch.captain(seen, "", False, T, T + 33)
    assert lines == []
    seen, lines = watch.captain(seen, "", False, T + 34, T + 34)
    assert lines == [RUNNING]
    _, lines = watch.captain(seen, "", False, T + 35, T + 35)
    assert lines == []


def test_the_first_look_at_the_engine_is_silent_either_way():
    _, lines = watch.captain(Seen("", None, 0, T), "", False, T, T + 1)
    assert lines == []
    seen, lines = watch.captain(Seen("", None, 0, T), "", False, None, T + 1)
    assert lines == []
    assert seen.engine is False


def test_a_gap_over_thirty_seconds_reports_nothing_that_call():
    seen, lines = watch.captain(Seen("", True, 0, T), "k1", False, T, T + 3600)
    assert lines == []
    assert seen == Seen("", True, 0, T + 3600)
    _, lines = watch.captain(seen, "", False, T + 3601, T + 3601)
    assert lines == []
    assert SLEEP_GAP == 30.0


def test_a_hand_rings_for_a_new_undelivered_seq_only():
    seen, lines = watch.hand(Seen("", None, 0, T), 0, T + 1)
    assert lines == []
    seen, lines = watch.hand(seen, 3, T + 2)
    assert lines == [MAIL]
    seen, lines = watch.hand(seen, 3, T + 3)
    assert lines == []
    seen, lines = watch.hand(seen, 0, T + 4)
    assert lines == []
    _, lines = watch.hand(seen, 4, T + 5)
    assert lines == [MAIL]
