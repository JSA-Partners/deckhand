from __future__ import annotations

import sqlite3
import stat
import threading
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path

import pytest

from deckhand.queue import DRAFT_HAND, STORY_HAND
from deckhand.step import Refusal
from deckhand.store import local
from deckhand.store.state import HEARTBEAT, LocalState, alive

T = 1_763_546_400.0
ISSUE = "7c1f3a52-0b7e-4c1e-9a55-3f7a1d2b9e10"


def ticking() -> Callable[[], str]:
    count = iter(range(1000))
    return lambda: f"2026-11-19T10:00:{next(count):02d}.000Z"


OPENED: list[LocalState] = []


@pytest.fixture(autouse=True)
def close_every_state() -> Iterator[None]:
    yield
    while OPENED:
        OPENED.pop().close()


def opened(path: Path, now: Callable[[], str] | None = None) -> LocalState:
    rows = LocalState(path, now=now)
    OPENED.append(rows)
    return rows


def state(tmp_path: Path, now: Callable[[], str] | None = None) -> LocalState:
    return opened(tmp_path / "deckhand.db", now=now)


def test_a_state_used_in_a_with_block_closes_its_connection(tmp_path: Path) -> None:
    with LocalState(tmp_path / "deckhand.db") as rows:
        rows.put("x", {"n": 1})
    with pytest.raises(Refusal):
        rows.get("x")


def test_a_new_file_is_owner_only(tmp_path: Path) -> None:
    state(tmp_path)
    assert stat.S_IMODE((tmp_path / "deckhand.db").stat().st_mode) == 0o600


def test_a_posted_message_is_pending_for_its_hand_in_order(tmp_path: Path) -> None:
    rows = state(tmp_path)
    rows.post("k1", "hand-ACME-1", "answer", "one")
    rows.post("k3", "hand-ACME-2", "answer", "three")
    rows.post("k2", "hand-ACME-1", "answer", "two")
    mine = rows.pending("hand-ACME-1")
    assert [m.key for m in mine] == ["k1", "k2"]
    assert mine[0].seq < mine[1].seq
    assert [m.key for m in rows.pending()] == ["k1", "k3", "k2"]


def test_a_second_post_of_one_key_writes_nothing(tmp_path: Path) -> None:
    rows = state(tmp_path)
    assert rows.post("k1", "hand-ACME-1", "answer", "first") is True
    assert rows.post("k1", "hand-ACME-1", "answer", "second") is False
    assert [m.body for m in rows.pending()] == ["first"]


def test_ack_through_a_seq_clears_only_that_hand(tmp_path: Path) -> None:
    rows = state(tmp_path)
    rows.post("k1", "hand-ACME-1", "answer", "one")
    rows.post("k2", "hand-ACME-1", "answer", "two")
    rows.post("k3", "hand-ACME-2", "answer", "three")
    assert rows.ack("hand-ACME-1", 2) == 2
    assert rows.pending("hand-ACME-1") == []
    assert [m.key for m in rows.pending()] == ["k3"]


def test_delivered_stamps_the_named_rows(tmp_path: Path) -> None:
    rows = state(tmp_path, now=lambda: "2026-11-19T10:00:00.000Z")
    rows.post("k1", "hand-ACME-1", "answer", "one")
    rows.post("k2", "hand-ACME-1", "answer", "two")
    first = rows.pending()[0].seq
    rows.delivered([first])
    assert [m.delivered for m in rows.pending()] == ["2026-11-19T10:00:00.000Z", None]


def test_want_then_launched_then_adopt_fill_one_row(tmp_path: Path) -> None:
    rows = state(tmp_path)
    rows.want("acme/widgets#ACME-1", "ACME-1", ISSUE, STORY_HAND, "/work/acme/widgets/.worktrees/acme-1")
    rows.launched("acme/widgets#ACME-1", "1444880d")
    rows.adopt("acme/widgets#ACME-1", "1444880d", "sid-full")
    row = rows.hands()["acme/widgets#ACME-1"]
    assert (row.ref, row.issue, row.kind) == ("ACME-1", ISSUE, STORY_HAND)
    assert (row.short, row.session, row.turn_ended) == ("1444880d", "sid-full", None)


def test_want_again_for_the_same_kind_keeps_the_session(tmp_path: Path) -> None:
    rows = state(tmp_path, now=ticking())
    rows.want("hand", "ACME-1", ISSUE, STORY_HAND, "/old")
    rows.adopt("hand", "1444880d", "sid-full")
    rows.restarted("hand")
    before = rows.hands()["hand"]
    rows.want("hand", "ACME-1", ISSUE, STORY_HAND, "/new")
    after = rows.hands()["hand"]
    assert (after.short, after.session, after.restarts, after.wanted) == (
        before.short,
        before.session,
        before.restarts,
        before.wanted,
    )
    assert after.cwd == "/new"


def test_want_for_another_kind_forgets_the_session(tmp_path: Path) -> None:
    rows = state(tmp_path, now=ticking())
    rows.want("hand", "ACME-1", ISSUE, DRAFT_HAND, "/work")
    rows.adopt("hand", "1444880d", "sid-full")
    rows.restarted("hand")
    first = rows.hands()["hand"].wanted
    rows.want("hand", "ACME-1", ISSUE, STORY_HAND, "/work")
    row = rows.hands()["hand"]
    assert (row.kind, row.short, row.session, row.restarts) == (STORY_HAND, None, None, 0)
    assert row.wanted > first


def test_restarted_counts_up_from_zero(tmp_path: Path) -> None:
    rows = state(tmp_path)
    rows.want("hand", "ACME-1", ISSUE, "spike", "/work")
    assert rows.hands()["hand"].restarts == 0
    rows.restarted("hand")
    rows.restarted("hand")
    assert rows.hands()["hand"].restarts == 2


def test_turn_ended_stamps_the_hand(tmp_path: Path) -> None:
    rows = state(tmp_path, now=lambda: "2026-11-19T10:00:00.000Z")
    rows.want("hand", "ACME-1", ISSUE, STORY_HAND, "/work")
    rows.turn_ended("hand")
    assert rows.hands()["hand"].turn_ended == "2026-11-19T10:00:00.000Z"


def test_alive_holds_for_thirty_seconds_after_a_beat() -> None:
    assert alive(None, T) is False
    assert alive(T, T + 30) is True
    assert alive(T, T + 30.1) is False


def test_an_engine_row_round_trips(tmp_path: Path) -> None:
    rows = state(tmp_path)
    assert rows.get("snapshot") is None
    rows.put("snapshot", {"top": ["ACME-1"]})
    assert rows.get("snapshot") == {"top": ["ACME-1"]}


def test_a_beat_reads_back_as_the_heartbeat(tmp_path: Path) -> None:
    rows = state(tmp_path)
    assert rows.heartbeat() is None
    rows.beat(42, T)
    assert rows.heartbeat() == (42, T)


def test_dropping_the_heartbeat_key_clears_it(tmp_path: Path) -> None:
    rows = state(tmp_path)
    rows.beat(42, T)
    rows.drop(HEARTBEAT)
    assert rows.heartbeat() is None


def test_changed_sees_another_connection_and_not_its_own_write(tmp_path: Path) -> None:
    a, b = state(tmp_path), state(tmp_path)
    assert a.changed() is True
    a.put("x", {"n": 1})
    assert a.changed() is False
    b.put("y", {"n": 2})
    assert a.changed() is True
    assert a.changed() is False


def test_two_states_write_at_once_without_locking_errors(tmp_path: Path) -> None:
    path = tmp_path / "deckhand.db"
    errors: list[Exception] = []

    def write(start: int) -> None:
        try:
            with LocalState(path) as rows:
                for n in range(start, 200, 2):
                    rows.post(f"k{n:03d}", "hand-ACME-1", "answer", str(n))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(opened(path).pending()) == 200


def test_opening_an_up_to_date_file_is_not_a_change(tmp_path: Path) -> None:
    a = state(tmp_path)
    assert a.changed() is True
    state(tmp_path)
    assert a.changed() is False


def test_a_version_1_file_gains_the_new_tables(tmp_path: Path) -> None:
    path = tmp_path / "deckhand.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE records (issue TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL, "
            "body TEXT NOT NULL, written TEXT NOT NULL, PRIMARY KEY (issue, kind, subject))"
        )
        db.execute("INSERT INTO records VALUES ('i', 'tier', 's', '{}', 'w')")
        db.execute("PRAGMA user_version=1")
    opened(path).want("hand", "ACME-1", ISSUE, STORY_HAND, "/work")
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT issue FROM records").fetchall() == [("i",)]
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert db.execute("SELECT restarts FROM hands").fetchall() == [(0,)]


def test_a_newer_file_is_not_stamped_back(tmp_path: Path) -> None:
    path = tmp_path / "deckhand.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA user_version=3")
    opened(path)
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_a_corrupt_file_refuses_in_the_records_wording(tmp_path: Path) -> None:
    path = tmp_path / "deckhand.db"
    path.write_bytes(b"not a database, just some bytes" * 100)
    with pytest.raises(Refusal) as refused:
        opened(path)
    assert refused.value.rule == f"deckhand.db in the state folder {tmp_path} is corrupt"


def test_a_write_past_the_busy_timeout_refuses_in_the_records_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "deckhand.db"
    opened(path)
    monkeypatch.setattr(local, "BUSY_SECONDS", 0)
    rows = opened(path)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(Refusal) as refused:
            rows.post("k1", "hand-ACME-1", "answer", "one")
    finally:
        holder.close()
    assert refused.value.rule == f"deckhand.db in the state folder {tmp_path} is locked by another process"
    assert rows.pending() == []


def test_a_read_that_meets_a_broken_table_refuses_in_the_records_wording(tmp_path: Path) -> None:
    path = tmp_path / "deckhand.db"
    rows = opened(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE messages")
    with pytest.raises(Refusal) as refused:
        rows.pending()
    assert refused.value.rule == f"deckhand.db in the state folder {tmp_path} cannot be used"


def test_a_launch_clears_the_last_turn_end(tmp_path: Path) -> None:
    rows = state(tmp_path)
    rows.want("hand", "ACME-1", ISSUE, STORY_HAND, "/work")
    rows.turn_ended("hand")
    rows.launched("hand", "1444880d")
    assert rows.hands()["hand"].turn_ended is None


def test_want_for_another_kind_clears_the_last_turn_end(tmp_path: Path) -> None:
    rows = state(tmp_path)
    rows.want("hand", "ACME-1", ISSUE, DRAFT_HAND, "/work")
    rows.turn_ended("hand")
    rows.want("hand", "ACME-1", ISSUE, STORY_HAND, "/work")
    assert rows.hands()["hand"].turn_ended is None


def test_a_heartbeat_of_another_shape_reads_as_none(tmp_path: Path) -> None:
    with LocalState(tmp_path / "deckhand.db") as rows:
        for body in ({"pid": 1}, {"pid": "1", "at": 2.0}, {"pid": 1, "at": "soon"}):
            rows.put("heartbeat", body)
            assert rows.heartbeat() is None
