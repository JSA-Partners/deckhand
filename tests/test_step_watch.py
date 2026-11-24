from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from deckhand import status, watch
from deckhand.step import Refusal, hand_may_apply
from deckhand.steps.watch import POLL, Watch
from deckhand.store.state import LocalState
from tests.test_status import question, snapshot, write


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    return tmp_path / "deckhand.db"


def passes(actions: dict[int, Callable[[], None]], last: int) -> Callable[[float], None]:
    count = 0

    def sleep(seconds: float) -> None:
        nonlocal count
        assert seconds == POLL
        count += 1
        if count in actions:
            actions[count]()
        if count >= last:
            raise StopIteration

    return sleep


def test_the_watch_command_prints_a_line_when_a_row_appears(db: Path, capsys, monkeypatch):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")

    def post() -> None:
        with LocalState(db) as rows:
            rows.post("k1", "hand-ACME-1", "answer", "Go ahead.")
            rows.post("k2", "hand-ACME-2", "answer", "not yours")

    with pytest.raises(StopIteration):
        Watch(sleep=passes({2: post}, 4)).apply(["hand"])
    assert capsys.readouterr().out == watch.MAIL + "\n"


def test_the_captain_watcher_starts_silent_with_an_item_already_on_top(db: Path, capsys):
    write(db.parent, snapshot([question("q1")]))
    with pytest.raises(StopIteration):
        Watch(sleep=passes({}, 3)).apply(["captain"])
    assert capsys.readouterr().out == ""


def test_the_captain_watcher_rings_for_a_new_top_item(db: Path, capsys):
    write(db.parent, snapshot([question("q1")]))

    def newer() -> None:
        write(db.parent, snapshot([question("q2")]))

    with pytest.raises(StopIteration):
        Watch(sleep=passes({2: newer}, 4)).apply(["captain"])
    assert capsys.readouterr().out == watch.QUEUE + "\n"


def test_the_captain_watcher_keeps_its_top_across_a_failed_snapshot_read(db: Path, capsys):
    write(db.parent, snapshot([question("q1")]))

    def broken() -> None:
        with LocalState(db) as rows:
            rows.put(status.SNAPSHOT, {"items": "unreadable"})

    def again() -> None:
        write(db.parent, snapshot([question("q1")]))

    def newer() -> None:
        write(db.parent, snapshot([question("q2")]))

    with pytest.raises(StopIteration):
        Watch(sleep=passes({2: broken, 3: again, 5: newer}, 6)).apply(["captain"])
    assert capsys.readouterr().out == watch.QUEUE + "\n"


def test_the_captain_watcher_rings_the_same_item_when_linear_reads_clean_again(db: Path, capsys):
    write(db.parent, status.stale(snapshot([question("q1")]), "2026-11-24T09:00:00.000Z"))

    def recovered() -> None:
        write(db.parent, snapshot([question("q1")]))

    with pytest.raises(StopIteration):
        Watch(sleep=passes({2: recovered}, 4)).apply(["captain"])
    assert capsys.readouterr().out == watch.QUEUE + "\n"


def test_an_engine_that_restarts_rings_once_its_snapshot_is_back(db: Path, capsys):
    now = [1795000000.0]
    write(db.parent, snapshot([question("q1")]))
    with LocalState(db) as rows:
        rows.beat(1, now[0])

    def later(then: Callable[[], None] = lambda: None) -> Callable[[], None]:
        def step() -> None:
            now[0] += 20
            then()

        return step

    def restarted() -> None:
        with LocalState(db) as rows:
            rows.drop(status.SNAPSHOT)
            rows.beat(2, now[0])

    def written() -> None:
        write(db.parent, snapshot([question("q1")]))
        with LocalState(db) as rows:
            rows.beat(2, now[0])

    steps = {1: later(), 2: later(), 3: later(restarted), 4: later(), 5: later(written)}
    with pytest.raises(StopIteration):
        Watch(sleep=passes(steps, 6), clock=lambda: now[0]).apply(["captain"])
    assert capsys.readouterr().out.splitlines() == [watch.STOPPED, watch.RUNNING, watch.QUEUE]


def test_the_captain_watcher_beats_its_own_row(db: Path):
    now = [1795000000.0]

    def later() -> None:
        now[0] += watch.BEAT_EVERY

    with pytest.raises(StopIteration):
        Watch(sleep=passes({1: later}, 2), clock=lambda: now[0]).apply(["captain"])
    with LocalState(db) as rows:
        assert watch.heard(rows) == now[0]


def test_a_hand_may_run_only_the_hand_watcher(monkeypatch):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    assert hand_may_apply("watch", ["hand"])
    assert not hand_may_apply("watch", ["captain"])


def test_a_spike_watcher_rings_for_its_own_mail(db: Path, monkeypatch, capsys):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_HAND_KIND", "spike")
    monkeypatch.setenv("DECKHAND_SPIKE", "a1b2c3d4e5f6")

    def post() -> None:
        with LocalState(db) as rows:
            rows.post("k1", "hand-a1b2c3d4e5f6", "answer", "not the spike's")
            rows.post("k2", "spike-a1b2c3d4e5f6", "answer", "Go ahead.")

    with pytest.raises(StopIteration):
        Watch(sleep=passes({2: post}, 4)).apply(["hand"])
    assert capsys.readouterr().out == watch.MAIL + "\n"


def test_a_hand_watcher_with_no_ref_parks_without_printing(db: Path, monkeypatch, capsys):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    with LocalState(db) as rows:
        rows.post("k1", "hand-", "answer", "Go ahead.")
    with pytest.raises(StopIteration):
        Watch(sleep=passes({}, 5)).apply(["hand"])
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("args", [[], ["engine"], ["hand", "x"]])
def test_watch_apply_takes_captain_or_hand(args):
    with pytest.raises(Refusal, match="watch apply captain\\|hand"):
        Watch(sleep=passes({}, 1)).apply(args)
