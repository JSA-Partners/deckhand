from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from deckhand import asks, launch, queue
from deckhand.acts import Acts, unheard
from deckhand.linear.client import LinearError
from deckhand.reconcile import Adopt, Launch, Post, Remove, Resume, Stop
from deckhand.records import HAND, Record
from deckhand.store.fake import FakeStore
from deckhand.store.local import LocalRecords
from deckhand.store.state import LocalState
from tests.shell import Shell, row
from tests.test_reconcile import story
from tests.test_step_hand import ME, building, clone, state

__all__ = ["clone", "state"]

NAME = "hand-ACME-1"
WORKTREE = ("widgets", ".worktrees", "acme-1-orders")
FETCH = ["git", "fetch", "origin", "--prune"]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path / "deckhand.db"


@pytest.fixture
def rows(db: Path) -> Iterator[LocalState]:
    with LocalState(db) as found:
        yield found


def acts(rows: LocalState, db: Path, shell: Shell, person: FakeStore | None = None) -> Acts:
    store = person if person is not None else building()
    return Acts(rows, LocalRecords(db), lambda: store, shell)


def a_launch(store: FakeStore, clone: Path, restart: bool = False) -> Launch:
    return Launch(NAME, "ACME-1", queue.STORY_HAND, clone, story(store, "ACME-1"), ME, restart)


def kinds(db: Path, issue: str = "s1") -> list[str]:
    return [r.kind for r in LocalRecords(db).records(issue)]


def argvs(shell: Shell) -> list[list[str]]:
    return [argv for argv, _ in shell.calls]


def test_a_launch_records_intent_fetches_then_starts_then_records_the_id(clone: Path, rows: LocalState, db: Path):
    store, shell = building(), Shell()
    assert acts(rows, db, shell, store).run(a_launch(store, clone)) is None
    worktree = clone / ".worktrees" / "acme-1-orders"
    calls = argvs(shell)
    assert calls.index(FETCH) < calls.index(
        ["git", "worktree", "add", "-b", "acme-1-orders", str(worktree), "origin/HEAD"]
    )
    [(argv, cwd)] = shell.launches()
    assert cwd == worktree and argv[-1] == "/deckhand:story ACME-1"
    held = rows.hands()[NAME]
    assert (held.cwd, held.issue, held.short, held.restarts) == (str(worktree), "s1", "1444880d", 0)
    assert asks.HAND_START in kinds(db)
    assert ("assign", "s1", ME) in store.writes


def test_a_launch_finding_a_live_session_adopts_it_and_starts_nothing(clone: Path, rows: LocalState, db: Path):
    store, shell = building(), Shell(row("ACME-1", "working", "busy"))
    before = list(store.writes)
    assert acts(rows, db, shell, store).run(a_launch(store, clone)) is None
    assert shell.launches() == [] and FETCH not in argvs(shell)
    held = rows.hands()[NAME]
    assert (held.short, held.session) == ("id-ACME-1", "sid-ACME-1")
    assert kinds(db) == [] and store.writes == before


def test_a_failed_fetch_starts_nothing_and_returns_a_line(clone: Path, rows: LocalState, db: Path):
    store, shell = building(), Shell(fail=(("git", "fetch"),))
    before = list(store.writes)
    line = acts(rows, db, shell, store).run(a_launch(store, clone))
    assert line == "ACME-1: cannot fetch origin: fatal: unable to access origin"
    assert not any(argv[:3] == ["git", "worktree", "add"] for argv in argvs(shell))
    assert shell.launches() == [] and kinds(db) == [] and store.writes == before


class Unreachable(FakeStore):
    def assign(self, issue: str, user: str) -> None:
        raise LinearError("unreachable", "Linear did not answer")


def test_a_failed_claim_keeps_the_hand_start_and_the_row(clone: Path, rows: LocalState, db: Path):
    store, shell = building(), Shell()
    line = Acts(rows, LocalRecords(db), Unreachable, shell).run(a_launch(store, clone))
    assert line == "ACME-1: unreachable: Linear did not answer"
    assert shell.launches() == []
    assert asks.HAND_START in kinds(db) and NAME in rows.hands()


def test_a_restart_launch_raises_the_count(clone: Path, rows: LocalState, db: Path):
    store = building()
    acts(rows, db, Shell(), store).run(a_launch(store, clone, restart=False))
    assert rows.hands()[NAME].restarts == 0
    acts(rows, db, Shell(), store).run(a_launch(store, clone, restart=True))
    assert rows.hands()[NAME].restarts == 1


def spike(clone: Path) -> Launch:
    return Launch("spike-k1", "k1", launch.SPIKE, clone, None, ME, False)


def test_a_spike_launch_claims_nothing_and_writes_no_hand_start(clone: Path, rows: LocalState, db: Path):
    store, shell = building(), Shell()
    before = list(store.writes)
    assert acts(rows, db, shell, store).run(spike(clone)) is None
    worktree = clone / ".worktrees" / "spike-k1"
    assert ["git", "worktree", "add", "--detach", str(worktree), "origin/HEAD"] in argvs(shell)
    [(argv, cwd)] = shell.launches()
    assert cwd == worktree and argv[-1] == "/deckhand:spike k1"
    assert store.writes == before and kinds(db, "") == []
    held = rows.hands()["spike-k1"]
    assert (held.kind, held.issue, held.short) == (launch.SPIKE, "", "1444880d")


def a_resume(record: Record | None = None, restart: bool = False, cwd: str = "/w/acme-1") -> Resume:
    return Resume(NAME, "sid-ACME-1", cwd, "Mail for you.", (1,), record, restart)


def wanted(rows: LocalState, short: str = "old1") -> None:
    rows.want(NAME, "ACME-1", "s1", queue.STORY_HAND, "/w/acme-1")
    rows.launched(NAME, short)
    rows.post("wake:s1:n1", NAME, asks.ANSWER, "body")


def test_a_resume_runs_by_full_id_in_the_saved_folder_and_marks_delivered(rows: LocalState, db: Path):
    wanted(rows)
    shell = Shell(row("ACME-1", "stopped"))
    assert acts(rows, db, shell).run(a_resume()) is None
    [(argv, cwd)] = shell.launches()
    assert (argv, cwd) == (launch.resume_argv("sid-ACME-1", "Mail for you."), Path("/w/acme-1"))
    [message] = rows.pending()
    assert message.delivered is not None
    assert rows.hands()[NAME].short == "1444880d"


def test_a_resume_with_a_start_record_writes_it_and_raises_the_count(rows: LocalState, db: Path):
    wanted(rows)
    start = Record(asks.HAND_START, "s1", "", HAND, {"kind": queue.STORY_HAND, "session": NAME})
    assert acts(rows, db, Shell(row("ACME-1", "stopped"))).run(a_resume(start, restart=True)) is None
    assert kinds(db) == [asks.HAND_START]
    assert rows.hands()[NAME].restarts == 1


def test_a_resume_of_a_live_session_runs_nothing(rows: LocalState, db: Path):
    wanted(rows)
    shell = Shell(row("ACME-1", "working", "busy"))
    assert acts(rows, db, shell).run(a_resume(restart=True)) is None
    assert shell.launches() == []
    assert rows.pending()[0].delivered is None and rows.hands()[NAME].restarts == 0


def test_a_resume_that_made_a_copy_stops_and_removes_the_copy(rows: LocalState, db: Path):
    wanted(rows)
    shell = Shell(row("ACME-1", "stopped"), copy="4a65b4bd")
    start = Record(asks.HAND_START, "s1", "", HAND, {"kind": queue.STORY_HAND, "session": NAME})
    line = acts(rows, db, shell).run(a_resume(start, restart=True))
    assert line == "ACME-1: the session was already running; its copy 4a65b4bd is removed"
    assert argvs(shell)[-2:] == [["claude", "stop", "4a65b4bd"], ["claude", "rm", "4a65b4bd"]]
    assert rows.pending()[0].delivered is None
    assert (rows.hands()[NAME].short, rows.hands()[NAME].restarts) == ("old1", 0) and kinds(db) == []


def wake(restart: bool = False) -> Post:
    record = Record(asks.WAKE, "s1", "n1", HAND, {"decision": "answer"})
    return Post(NAME, "wake:s1:n1", asks.ANSWER, "Decided.", "s1", record, restart)


def test_a_post_writes_the_row_and_its_record_once(rows: LocalState, db: Path):
    executor = acts(rows, db, Shell())
    assert executor.run(wake()) is None and executor.run(wake()) is None
    assert [m.key for m in rows.pending()] == ["wake:s1:n1"]
    assert kinds(db) == [asks.WAKE]


def test_a_restart_post_raises_the_count_once(rows: LocalState, db: Path):
    rows.want(NAME, "ACME-1", "s1", queue.STORY_HAND, "/w/acme-1")
    executor = acts(rows, db, Shell())
    executor.run(wake(restart=True))
    executor.run(wake(restart=True))
    assert rows.hands()[NAME].restarts == 1


def test_an_unheard_stop_records_its_seqs_only_once_the_stop_succeeds(rows: LocalState, db: Path):
    wanted(rows)
    failing = Shell(fail=(("claude", "stop"),))
    assert acts(rows, db, failing).run(Stop(NAME, "old1", "unheard", (1,))) is not None
    assert unheard(rows) == frozenset()
    shell = Shell()
    assert acts(rows, db, shell).run(Stop(NAME, "old1", "unheard", (1,))) is None
    assert argvs(shell) == [["claude", "stop", "old1"]]
    assert unheard(rows) == frozenset({1})


def test_an_unheard_seq_is_dropped_once_acknowledged(rows: LocalState, db: Path):
    wanted(rows)
    executor = acts(rows, db, Shell())
    executor.run(Stop(NAME, "old1", "unheard", (1,)))
    rows.ack(NAME, 1)
    rows.post("wake:s1:n2", NAME, asks.ANSWER, "body")
    executor.run(Stop(NAME, "old1", "unheard", (2,)))
    assert unheard(rows) == frozenset({2})


def test_a_remove_stops_then_removes_and_an_adopt_records_the_session(rows: LocalState, db: Path):
    wanted(rows)
    shell = Shell()
    executor = acts(rows, db, shell)
    assert executor.run(Remove(NAME, "c0py")) is None
    assert argvs(shell) == [["claude", "stop", "c0py"], ["claude", "rm", "c0py"]]
    assert executor.run(Adopt(NAME, "n3w", "sid-new")) is None
    assert (rows.hands()[NAME].short, rows.hands()[NAME].session) == ("n3w", "sid-new")


def test_an_unexpected_error_becomes_a_line_without_its_text(rows: LocalState, db: Path):
    def broken(argv, cwd=None, stdin=None):
        raise RuntimeError("secret detail")

    line = Acts(rows, LocalRecords(db), building, broken).run(Stop(NAME, "old1", "finished"))
    assert line == "ACME-1: RuntimeError"
