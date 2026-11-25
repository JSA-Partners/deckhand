from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from deckhand import asks, chores, config, features, fleet, launch, mirror, queue, stalls
from deckhand.actions import Adopt, Attach, Launch, Leave, Post, PutDocument, Remove, Resume, Stop
from deckhand.acts import Acts, unheard
from deckhand.linear.client import LinearError
from deckhand.policy import Policy
from deckhand.reading import Reading
from deckhand.reconcile import Facts, reconcile
from deckhand.records import HAND, Record
from deckhand.store.fake import FakeStore
from deckhand.store.local import LocalRecords
from deckhand.store.state import LocalState
from tests.shell import Shell, World, row
from tests.test_mirror import CONTRACT, named
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
    assert calls.count(FETCH) == 1
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
    assert argvs(shell).count(FETCH) == 1
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


def test_a_leave_writes_its_records_once_and_runs_nothing(rows: LocalState, db: Path):
    data = {"n": 1, "text": "rotate", "why": "its hand stopped after the merge", "session": "sid-1"}
    left = Record(chores.AFTER_PERSON, "s1:1", "v1", "agent:engine", data)
    shell = Shell()
    executor = acts(rows, db, shell)
    assert executor.run(Leave(NAME, "s1", (left,))) is None and executor.run(Leave(NAME, "s1", (left,))) is None
    assert kinds(db) == [chores.AFTER_PERSON] and shell.calls == []


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


def test_a_remove_of_a_stopped_copy_only_removes_it(rows: LocalState, db: Path):
    shell = Shell()
    assert acts(rows, db, shell).run(Remove(NAME, "c0py", stop=False)) is None
    assert argvs(shell) == [["claude", "rm", "c0py"]]


def test_an_unexpected_error_becomes_a_line_without_its_text(rows: LocalState, db: Path):
    def broken(argv, cwd=None, stdin=None):
        raise RuntimeError("secret detail")

    line = Acts(rows, LocalRecords(db), building, broken).run(Stop(NAME, "old1", "finished"))
    assert line == "ACME-1: RuntimeError"


def down() -> FakeStore:
    raise LinearError("unreachable", "down")


class Loop:
    def __init__(self, rows: LocalState) -> None:
        self.rows, self.store, self.world = rows, building(), World()
        self.person: Callable[[], FakeStore] = lambda: self.store
        self.acts = Acts(rows, self.store, lambda: self.person(), self.world)

    def tick(self) -> list[str | None]:
        clones = config.local().clones
        reading = Reading("ACME", features.current(self.store, "ACME"), ME, [], {}, [])
        policies = {path: Policy.load(path) for path in clones.values()}
        read = fleet.sessions(self.world)
        hands, pending = self.rows.hands(), self.rows.pending()
        found = Facts(time.time(), 0.0, read, hands, pending, reading, 1, clones=clones, policies=policies)
        return [self.acts.run(action) for action in reconcile(found, self.store) if action.name == NAME]

    def running(self) -> None:
        assert self.tick() == [None] and self.tick() == [None]

    def restarts(self) -> int:
        return self.rows.hands()[NAME].restarts


@pytest.mark.parametrize("broken", ["claim", "launch"])
def test_a_first_launch_that_fails_once_spends_no_restart(clone: Path, rows: LocalState, broken: str):
    loop = Loop(rows)
    if broken == "claim":
        loop.person = down
    else:
        loop.world.fail.add(("claude", "--bg"))
    assert loop.tick() != [None]
    loop.person, loop.world.fail = lambda: loop.store, set()
    loop.running()
    assert loop.restarts() == 0 and len(loop.world.rows) == 1


def stopped_after_one_restart(loop: Loop) -> None:
    loop.running()
    loop.world.stop(NAME)
    assert loop.tick() == [None]
    loop.world.rows.clear()


def test_a_relaunch_after_progress_that_fails_is_retried(clone: Path, rows: LocalState):
    loop = Loop(rows)
    stopped_after_one_restart(loop)
    loop.store.put_record("s1", Record("note", "s1", "v1", HAND, {}))
    loop.world.fail.add(("claude", "--bg"))
    assert loop.tick() == ["ACME-1: claude --bg failed: error: one; fatal: two"]
    loop.world.fail.clear()
    launched = loop.world.launches()
    assert loop.tick() == [None] and loop.world.launches() == launched + 1


def test_a_restart_answer_whose_launch_fails_is_retried(clone: Path, rows: LocalState):
    loop = Loop(rows)
    stopped_after_one_restart(loop)
    start = queue.last_start(loop.store.records("s1"))
    assert start is not None
    loop.store.put_record("s1", Record(stalls.STALLED, "s1", start.at, "person:session-1", {"answer": stalls.RESTART}))
    loop.world.fail.add(("claude", "--bg"))
    assert loop.tick() != [None]
    loop.world.fail.clear()
    launched = loop.world.launches()
    assert loop.tick() == [None] and loop.world.launches() == launched + 1


def test_a_silent_relaunch_whose_fleet_read_fails_still_counts_its_restart(clone: Path, rows: LocalState):
    loop = Loop(rows)
    loop.running()
    loop.world.rows.clear()
    loop.world.silent = True
    loop.world.reads_before_failing = sum(call[:2] == ["claude", "agents"] for call in loop.world.calls) + 2
    assert loop.tick() != [None]
    assert loop.restarts() == 1


def test_a_silent_relaunch_whose_fleet_read_failed_is_adopted_on_the_next_tick(clone: Path, rows: LocalState):
    loop = Loop(rows)
    loop.running()
    old = rows.hands()[NAME].short
    loop.world.rows.clear()
    loop.world.silent = True
    loop.world.reads_before_failing = sum(call[:2] == ["claude", "agents"] for call in loop.world.calls) + 2
    assert loop.tick() != [None]
    assert rows.hands()[NAME].short == old
    restarts, launched = loop.restarts(), loop.world.launches()
    loop.world.silent, loop.world.reads_before_failing = False, None
    assert loop.tick() == [None]
    [running] = [found for found in loop.world.rows if found["name"] == NAME]
    held = rows.hands()[NAME]
    assert (held.short, held.session) == (running["id"], running["sessionId"])
    assert loop.tick() == []
    assert loop.world.launches() == launched and loop.restarts() == restarts


def test_a_silent_resume_reads_its_short_id_from_the_fleet(rows: LocalState, db: Path):
    wanted(rows)
    world = World()
    world.rows.append({"id": "old1", "name": NAME, "sessionId": "sid-ACME-1", "cwd": "/w", "state": "stopped"})
    world.silent = True
    assert Acts(rows, LocalRecords(db), building, world).run(a_resume()) is None
    assert rows.hands()[NAME].short == world.rows[-1]["id"] != "old1"


def test_a_failed_resume_delivers_nothing_writes_nothing_and_counts_nothing(rows: LocalState, db: Path):
    wanted(rows)
    start = Record(asks.HAND_START, "s1", "", HAND, {"kind": queue.STORY_HAND, "session": NAME})
    line = acts(rows, db, Shell(fail=(("claude", "--bg"),))).run(a_resume(start, restart=True))
    assert line == "ACME-1: claude --bg failed: fatal: unable to access origin"
    assert rows.pending()[0].delivered is None and kinds(db) == [] and rows.hands()[NAME].restarts == 0


def test_a_failure_of_several_lines_reads_as_one(rows: LocalState, db: Path):
    world = World()
    world.fail.add(("claude", "stop"))
    line = Acts(rows, LocalRecords(db), building, world).run(Stop(NAME, "old1", "finished"))
    assert line == "ACME-1: claude stop failed: error: one; fatal: two"


def launched_settings(shell: Shell) -> tuple[dict, Path]:
    [(argv, cwd)] = shell.launches()
    return json.loads(argv[argv.index("--settings") + 1]), cwd


@pytest.mark.parametrize(
    ("kind", "variable", "ref"), [("story", "DECKHAND_STORY", "ACME-1"), ("spike", "DECKHAND_SPIKE", "k1")]
)
def test_an_engine_launch_carries_the_hands_settings(
    clone: Path, rows: LocalState, db: Path, kind: str, variable: str, ref: str
):
    store, shell = building(), Shell()
    action = a_launch(store, clone) if kind == "story" else spike(clone)
    assert acts(rows, db, shell, store).run(action) is None
    settings, worktree = launched_settings(shell)
    env = settings["env"]
    assert (env["DECKHAND_ROLE"], env[variable], env["DECKHAND_HAND_KIND"]) == ("hand", ref, kind)
    assert not ({"DECKHAND_STORY", "DECKHAND_SPIKE"} - {variable}) & set(env)
    deny = settings["permissions"]["deny"]
    for form in (worktree, worktree.resolve()):
        assert f"Edit(//{str(form / '.git').lstrip('/')})" in deny
        assert f"Edit(//{str(form / '.claude/settings.json').lstrip('/')})" in deny
    assert str(worktree / ".git") in settings["sandbox"]["filesystem"]["denyWrite"]


class Refusing(FakeStore):
    def attach(self, issue: str, url: str, title: str, subtitle: str, metadata: dict[str, str | int]) -> None:
        raise LinearError("network", "Linear is unreachable")


def mirrors(store: FakeStore) -> list[Attach | PutDocument]:
    feature = features.current(named(store), "ACME")
    assert feature is not None
    return mirror.wanted(feature, CONTRACT)


def test_an_attach_writes_through_the_app_store_then_its_digest(rows: LocalState, db: Path):
    store = building()
    first = mirrors(store)[0]
    app = FakeStore()
    runs = Acts(rows, LocalRecords(db), lambda: store, Shell(), app=lambda: app)
    assert runs.run(first) is None
    assert app.writes == [("attach", "s1", first.url)]
    assert rows.keyed(mirror.PREFIX) == {first.key: {"digest": first.digest}}


def test_a_put_document_writes_the_story_map_then_its_digest(rows: LocalState, db: Path):
    app = FakeStore()
    document = PutDocument("mirror:p1", "mirror:p1:story-map", "d1", "doc-1", "p1", "Story map", "## ACME-1\n")
    runs = Acts(rows, LocalRecords(db), lambda: app, Shell(), app=lambda: app)
    assert runs.run(document) is None
    assert app.document("doc-1") == {"id": "doc-1", "parent": "p1", "title": "Story map", "body": "## ACME-1\n"}
    assert rows.keyed(mirror.PREFIX) == {"mirror:p1:story-map": {"digest": "d1"}}


def test_a_failed_attach_leaves_no_digest_and_is_due_next_read(rows: LocalState, db: Path):
    store = building()
    first = mirrors(store)[0]
    runs = Acts(rows, LocalRecords(db), lambda: store, Shell(), app=Refusing)
    assert runs.run(first) == "mirror:s1: network: Linear is unreachable"
    assert rows.keyed(mirror.PREFIX) == {}
    feature = features.current(store, "ACME")
    assert feature is not None
    done = {key: body["digest"] for key, body in rows.keyed(mirror.PREFIX).items()}
    assert first in mirror.due(feature, CONTRACT, done)
