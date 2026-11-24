from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from deckhand import acts, answers, asks, engine, fleet, linker, queue, status, watch
from deckhand.engine import Engine
from deckhand.linear.client import LinearError
from deckhand.linker import Linked
from deckhand.reconcile import RETRY_AFTER, Action, Launch, Post, Remove, Resume, Stop
from deckhand.records import iso
from deckhand.step import Refusal
from deckhand.store.cached import CachedStore
from deckhand.store.fake import FakeStore
from deckhand.store.local import default_path
from deckhand.store.open import Stores
from deckhand.store.state import LocalState
from deckhand.worker import Worker
from tests.shell import Shell, row
from tests.test_queue import asked, started
from tests.test_step_hand import building, clone, configure, state

__all__ = ["clone", "state"]

T = 1795000000.0
DAY = date(2026, 11, 17)
NAME = "hand-ACME-1"


class Clock:
    def __init__(self) -> None:
        self.t = T

    def __call__(self) -> float:
        return self.t


class FakePoller:
    def __init__(self, value: Any = None) -> None:
        self.value, self.at, self.pokes, self.stopped = value, None, 0, False

    def latest(self) -> Any:
        return self.value

    def began(self) -> float | None:
        return self.at

    def poke(self) -> None:
        self.pokes += 1

    def stop(self) -> None:
        self.stopped = True


class FakeWorker:
    def __init__(self) -> None:
        self.submitted: list[Action] = []
        self.running: set[str] = set()
        self.done: list[tuple[Action, str | None]] = []

    def submit(self, action: Action) -> None:
        self.submitted.append(action)
        self.running.add(action.name)

    def busy(self) -> frozenset[str]:
        return frozenset(self.running)

    def results(self) -> list[tuple[Action, str | None]]:
        found, self.done = self.done, []
        return found

    def finish(self, action: Action, line: str | None = None) -> None:
        self.running.discard(action.name)
        self.done.append((action, line))

    def stop(self) -> None:
        pass

    def working(self) -> bool:
        return False


class Down:
    def __init__(self, inner: FakeStore) -> None:
        self.inner, self.down, self.calls = inner, False, 0

    def __getattr__(self, name: str) -> Callable[..., Any]:
        method = getattr(self.inner, name)

        def called(*args: Any, **kwargs: Any) -> Any:
            if name not in ("records", "put_record", "recorded"):
                self.calls += 1
                if self.down:
                    raise LinearError("network", "Linear is unreachable")
            return method(*args, **kwargs)

        return called


class Rig:
    def __init__(self, store: FakeStore, *rows: dict) -> None:
        self.store, self.down = store, Down(store)
        self.clock = Clock()
        self.fleets = FakePoller(fleet.sessions(Shell(*rows)))
        self.linear = FakePoller()
        self.worker = FakeWorker()
        self.state = LocalState(default_path(), now=lambda: iso(int(self.clock() * 1000)))
        self.engine = Engine("ACME", self.state, self.fleets, self.linear, self.worker, self.clock, lambda: DAY, 42)

    def link(self) -> Linked:
        stores = Stores(app=CachedStore(self.down), person=self.store)
        found = linker.link("ACME", self.fleets.latest, self.clock, lambda: stores, lambda: DAY)
        self.linear.value = found
        return found

    def sessions(self, *rows: dict) -> None:
        self.fleets.value = fleet.sessions(Shell(*rows))
        self.landed()

    def landed(self) -> None:
        self.fleets.at = self.clock()

    def step(self, seconds: float = 1.0) -> None:
        self.clock.t += seconds
        self.engine.tick()

    def wait(self, seconds: float) -> None:
        for _ in range(int(seconds // 5)):
            self.landed()
            self.step(5)

    def ready(self) -> None:
        self.link()
        self.clock.t += 0.5
        self.landed()
        self.engine.tick()

    def shown(self) -> dict:
        found = status.read(answers.state_dir())
        assert found is not None
        return found

    def kinds(self) -> list[str]:
        return [item["kind"] for item in self.shown()["items"]]


@pytest.fixture
def rig(clone: Path) -> Rig:
    configure(clone, 1)
    return Rig(building())


def held_hand(rig: Rig, restarts: int = 0) -> None:
    rig.state.want(NAME, "ACME-1", "s1", queue.STORY_HAND, "/w/acme-1")
    rig.state.adopt(NAME, "id-ACME-1", "sid-ACME-1")
    for _ in range(restarts):
        rig.state.restarted(NAME)


def test_the_first_tick_writes_a_heartbeat_and_then_every_five_seconds(rig: Rig):
    rig.engine.tick()
    assert rig.state.heartbeat() == (42, T)
    rig.step(1)
    assert rig.state.heartbeat() == (42, T)
    rig.step(4)
    assert rig.state.heartbeat() == (42, T + 5)


def test_a_link_writes_the_snapshot_and_an_unchanged_one_is_not_rewritten(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    writes = []
    real = status.put
    monkeypatch.setattr(status, "put", lambda rows, shown: writes.append(shown) or real(rows, shown))
    rig.ready()
    assert rig.shown()["feature"] == "Alpha" and rig.shown()["written_at"] == iso(int(T * 1000))
    rig.step()
    rig.step()
    assert len(writes) == 1


def test_a_new_record_recomputes_the_queue_without_a_linear_read(rig: Rig):
    rig.store.assign("s1", "viewer-1")
    rig.ready()
    rig.step()
    rig.step()
    assert "question" not in rig.kinds()
    calls = rig.down.calls
    asked(rig.store, "s1")
    rig.step()
    assert "question" in rig.kinds()
    assert rig.down.calls == calls


def test_reconcile_actions_reach_the_worker_once_while_busy(rig: Rig):
    rig.ready()
    assert [(type(a), a.name) for a in rig.worker.submitted] == [(Launch, NAME)]
    rig.step()
    rig.step()
    assert len(rig.worker.submitted) == 1


def test_a_failed_action_is_counted_and_its_line_is_the_note(rig: Rig):
    rig.ready()
    [launch] = rig.worker.submitted
    rig.worker.finish(launch, "ACME-1: cannot fetch origin")
    rig.step()
    rig.landed()
    rig.step()
    assert rig.engine.note() == "ACME-1: cannot fetch origin"
    assert len(rig.worker.submitted) == 1
    rig.wait(RETRY_AFTER)
    assert len(rig.worker.submitted) == 2


def test_a_turn_end_pokes_the_linear_poller(rig: Rig):
    held_hand(rig)
    rig.ready()
    pokes = rig.linear.pokes
    rig.clock.t += 2
    rig.state.turn_ended(NAME)
    rig.step()
    assert rig.linear.pokes == pokes + 1
    assert rig.engine.note() == engine.READING
    rig.step()
    assert rig.linear.pokes == pokes + 1
    rig.link()
    rig.step()
    assert rig.engine.note() != engine.READING


def test_a_gap_over_thirty_seconds_pokes_both_and_reconciles_nothing_that_tick(rig: Rig):
    rig.link()
    rig.engine.tick()
    rig.step(31)
    assert (rig.fleets.pokes, rig.linear.pokes) == (1, 1)
    assert rig.worker.submitted == []
    assert rig.state.heartbeat() == (42, T + 31)


def test_no_reconcile_until_a_fleet_read_and_a_link_land_after_a_wake(rig: Rig):
    rig.link()
    rig.landed()
    rig.engine.tick()
    rig.step(31)
    rig.step()
    assert rig.worker.submitted == []
    rig.landed()
    rig.step()
    assert rig.worker.submitted == []
    rig.link()
    rig.step()
    assert [a.name for a in rig.worker.submitted] == [NAME]


def test_linear_down_marks_the_snapshot_stale_and_still_delivers_mail(clone: Path):
    configure(clone, 1)
    rig = Rig(building(), row("ACME-1", "stopped"))
    held_hand(rig)
    rig.state.post("wake:s1:n1", NAME, asks.ANSWER, "Redis")
    started(rig.store, "s1")
    rig.sessions(row("ACME-1", "stopped"), row("ACME-2", "working", "busy"))
    rig.ready()
    assert rig.worker.submitted == []
    rig.sessions(row("ACME-1", "stopped"))
    rig.down.down = True
    rig.link()
    rig.step()
    assert rig.shown()["stale_since"] == iso(int((T + 1.5) * 1000))
    [resume] = rig.worker.submitted
    assert isinstance(resume, Resume) and resume.name == NAME and "Redis" in resume.text


def test_a_record_written_while_linear_is_down_still_reaches_the_queue(rig: Rig):
    rig.store.assign("s1", "viewer-1")
    rig.ready()
    rig.down.down = True
    rig.clock.t += 0.5
    rig.link()
    asked(rig.store, "s1")
    rig.step()
    assert "question" in rig.kinds()
    assert rig.shown()["stale_since"] is not None


def test_no_reconcile_before_the_first_good_link(rig: Rig):
    rig.down.down = True
    rig.link()
    rig.clock.t += 0.5
    rig.landed()
    rig.engine.tick()
    rig.step()
    assert rig.worker.submitted == []


def test_a_missing_personal_key_is_a_note_not_a_failed_link(rig: Rig):
    found = linker.link("ACME", rig.fleets.latest, rig.clock, lambda: Stores(app=CachedStore(rig.down)), lambda: DAY)
    assert found.error == "" and found.reading is not None and found.reading.me is None
    assert [note.split(":")[0] for note in found.reading.notes] == ["missing credentials file"]


def test_a_broken_repository_policy_is_one_item_per_clone_and_a_note_in_words(rig: Rig, clone: Path):
    rig.store.assign("s1", "viewer-1")
    rig.store.assign("s2", "viewer-1")
    asked(rig.store, "s1")
    (clone / ".claude" / "deckhand.toml").write_text('repository_label = ""\n')
    rig.ready()
    assert rig.worker.submitted == []
    note = "repository_label must be a non-empty string in .claude/deckhand.toml of clone widgets"
    assert rig.engine.note() == f"{note}; its stories do not start"
    [item] = [i for i in rig.shown()["items"] if i["kind"] == queue.POLICY]
    assert rig.kinds() == ["question", queue.POLICY]
    assert (item["ref"], item["lines"], item["next"]) == ("widgets", [note, queue.POLICY_LINE], [])
    (clone / ".claude" / "deckhand.toml").write_text('repository_label = "widgets"\n')
    rig.link()
    rig.step()
    assert rig.engine.note() == ""
    assert queue.POLICY not in rig.kinds()


def test_a_missing_repository_policy_says_so_without_a_path(rig: Rig, clone: Path):
    (clone / ".claude" / "deckhand.toml").unlink()
    rig.ready()
    assert rig.engine.note() == ".claude/deckhand.toml is missing in clone widgets; its stories do not start"
    assert rig.kinds() == [queue.POLICY]


def test_a_live_captain_whose_watcher_is_silent_is_the_note(rig: Rig):
    captain = row("x", "idle", "busy", at=int((T - 600) * 1000)) | {"name": "captain"}
    rig.sessions(captain)
    rig.ready()
    rig.wait(35)
    assert rig.engine.note() == watch.DEAF
    rig.state.put(watch.WATCHER, {"at": rig.clock()})
    assert rig.engine.note() == ""


def test_a_build_order_cycle_is_the_note(rig: Rig):
    rig.store.relate("s1", "s2")
    rig.store.relate("s2", "s1")
    rig.ready()
    assert rig.engine.note() == "build order cycle: ACME-1, ACME-2; break it in Linear"


def test_the_restart_counts_reach_the_read(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    held_hand(rig, restarts=1)
    seen = []
    real = engine.reading.read
    monkeypatch.setattr(engine.reading, "read", lambda *a, **k: seen.append(k.get("restarts")) or real(*a, **k))
    rig.ready()
    assert seen[-1] == {"ACME-1": 1}


def test_close_drops_the_heartbeat(rig: Rig):
    rig.engine.tick()
    rig.engine.close()
    with LocalState(default_path()) as rows:
        assert rows.heartbeat() is None
    assert rig.fleets.stopped and rig.linear.stopped


def test_a_failed_fleet_read_submits_nothing(rig: Rig):
    rig.fleets.value = fleet.Fleet([], "the fleet read failed: claude vanished")
    rig.ready()
    rig.step()
    assert rig.worker.submitted == []


def test_a_fresh_launch_shows_no_stalled_item_before_the_next_fleet_read(rig: Rig, clone: Path):
    rig.store.assign("s1", "viewer-1")
    held_hand(rig, restarts=1)
    started(rig.store, "s1")
    rig.ready()
    rig.step()
    assert queue.STALLED in rig.kinds() and NAME not in {a.name for a in rig.worker.submitted}
    rig.worker.finish(Launch(NAME, "ACME-1", queue.STORY_HAND, clone, None, "viewer-1"))
    rig.step()
    assert queue.STALLED not in rig.kinds()
    rig.step()
    assert queue.STALLED not in rig.kinds()
    rig.sessions(row("ACME-1", "working", "busy"))
    rig.step()
    assert queue.STALLED not in rig.kinds()
    rig.sessions()
    rig.step()
    assert queue.STALLED in rig.kinds()


def test_a_hand_stopped_as_unheard_shows_no_stalled_item_while_its_mail_waits(clone: Path):
    configure(clone, 1)
    rig = Rig(building(), row("ACME-1", "stopped"))
    rig.store.assign("s1", "viewer-1")
    held_hand(rig, restarts=1)
    started(rig.store, "s1")
    rig.state.post("wake:s1:n1", NAME, asks.ANSWER, "Redis")
    [message] = rig.state.pending()
    rig.state.put(acts.UNHEARD, {"seqs": [message.seq]})
    rig.ready()
    assert queue.STALLED not in rig.kinds()
    assert [(type(a), a.seqs) for a in rig.worker.submitted] == [(Resume, (message.seq,))]


def two_sessions(copy_state: str = "working") -> tuple[dict, dict]:
    copy = row("ACME-1", copy_state, "busy" if copy_state == "working" else None, at=1795000001000)
    return row("ACME-1", "working", "busy"), copy | {"id": "copy", "sessionId": "sid-copy"}


def test_a_completed_remove_is_not_repeated_on_a_stale_fleet_read_and_its_line_clears(clone: Path):
    configure(clone, 1)
    original, copy = two_sessions()
    rig = Rig(building(), original, copy)
    held_hand(rig)
    rig.ready()
    removal = Remove(NAME, "copy")
    assert rig.worker.submitted == [removal]
    rig.worker.finish(removal, "ACME-1: claude stop failed: error: no running session copy")
    rig.step()
    rig.step()
    assert rig.worker.submitted == [removal]
    assert rig.engine.note() == "ACME-1: claude stop failed: error: no running session copy"
    rig.clock.t += 0.5
    rig.sessions(original)
    rig.step()
    assert rig.worker.submitted == [removal] and rig.engine.note() == ""


def test_a_completed_stop_is_not_repeated_on_a_stale_fleet_read(clone: Path):
    configure(clone, 1)
    rig = Rig(building(), row("ACME-1", "idle", "idle"))
    held_hand(rig)
    started(rig.store, "s1")
    rig.store.set_issue_state("s1", "Done")
    rig.ready()
    [stop] = [a for a in rig.worker.submitted if a.name == NAME]
    assert isinstance(stop, Stop) and stop.why == "finished"
    rig.worker.finish(stop)
    rig.step()
    rig.step()
    assert [a.name for a in rig.worker.submitted].count(NAME) == 1


def test_after_a_failed_rm_a_fresh_engine_removes_the_stopped_copy_and_starts_no_second_hand(clone: Path):
    configure(clone, 1)
    original, copy = two_sessions("stopped")
    rig = Rig(building(), original, copy)
    held_hand(rig)
    rig.ready()
    assert rig.worker.submitted == [Remove(NAME, "copy", stop=False)]


def test_an_outage_with_a_done_sibling_still_delivers_mail_as_a_resume(clone: Path):
    configure(clone, 1)
    rig = Rig(building(), row("ACME-1", "stopped"))
    held_hand(rig)
    rig.state.post("wake:s1:n1", NAME, asks.ANSWER, "Redis")
    started(rig.store, "s1")
    rig.store.set_issue_state("s2", "Done")
    rig.link()
    rig.down.down = True
    calls = rig.down.calls
    rig.clock.t += 0.5
    rig.landed()
    rig.engine.tick()
    rig.step()
    assert [type(a) for a in rig.worker.submitted if a.name == NAME] == [Resume]
    assert rig.down.calls == calls


def test_stop_all_stops_every_alive_hand_and_spike(rig: Rig):
    spike = row("ACME-2", "working", "busy") | {"id": "id-spike", "name": "spike-k1"}
    captain = row("ACME-3", "working", "busy") | {"id": "id-captain", "name": "captain"}
    shell = Shell(row("ACME-1", "idle", "idle"), row("ACME-4", "stopped"), spike, captain)
    order = []
    rig.worker.stop = lambda: order.append("worker")
    rig.engine.stop_all(lambda argv, cwd=None, stdin=None: order.append(argv[1]) or shell(argv, cwd, stdin))
    stops = [argv for argv, _ in shell.calls if argv[1] == "stop"]
    assert stops == [["claude", "stop", "id-ACME-1"], ["claude", "stop", "id-spike"]]
    assert order[0] == "worker"


def test_a_launch_in_flight_past_five_seconds_during_stop_all_ends_stopped(rig: Rig):
    began, shell = threading.Event(), Shell(row("ACME-1", "idle", "idle"))

    class Slow:
        def run(self, action: Action) -> str | None:
            began.set()
            time.sleep(5.2)
            shell.rows.append(row("ACME-2", "working", "busy"))
            return None

    @contextmanager
    def opened() -> Iterator[Slow]:
        yield Slow()

    worker = Worker(opened)
    worker.start()
    worker.submit(Launch("hand-ACME-2", "ACME-2", "story", Path("/w"), None, "me"))
    assert began.wait(5)
    said = Engine("ACME", rig.state, rig.fleets, rig.linear, worker, rig.clock, lambda: DAY, 42, shell).stop_all()
    stops = [argv for argv, _ in shell.calls if argv[1] == "stop"]
    assert stops == [["claude", "stop", "id-ACME-1"], ["claude", "stop", "id-ACME-2"]] and said == []


def test_ctrl_c_during_stop_all_says_which_hands_stopped_and_which_did_not(rig: Rig):
    shell = Shell(row("ACME-1", "idle", "idle"), row("ACME-2", "idle", "idle"), row("ACME-3", "idle", "idle"))

    def runner(argv, cwd=None, stdin=None):
        if argv[:3] == ["claude", "stop", "id-ACME-2"]:
            raise KeyboardInterrupt
        return shell(argv, cwd, stdin)

    assert rig.engine.stop_all(runner) == ["stopped: ACME-1", "not stopped: ACME-2, ACME-3"]


def test_ctrl_c_while_stop_all_waits_for_the_worker_stops_no_hand(rig: Rig):
    def interrupted() -> None:
        raise KeyboardInterrupt

    rig.worker.stop = interrupted
    shell = Shell(row("ACME-1", "idle", "idle"))
    assert rig.engine.stop_all(shell) == ["stopped: none", "not stopped: every hand still running"]
    assert shell.calls == []


def test_finishing_is_true_only_while_an_action_runs():
    began, release = threading.Event(), threading.Event()

    class Held:
        def run(self, action: Action) -> str | None:
            began.set()
            release.wait(5)
            return None

    @contextmanager
    def opened() -> Iterator[Held]:
        yield Held()

    worker = Worker(opened)
    assert worker.working() is False
    worker.start()
    worker.submit(Launch("hand-ACME-2", "ACME-2", "story", Path("/w"), None, "me"))
    assert began.wait(5) and worker.working() is True
    release.set()
    worker.stop()
    assert worker.working() is False


def test_stop_all_runs_through_the_engines_runner_by_default(rig: Rig):
    shell = Shell(row("ACME-1", "idle", "idle"))
    Engine("ACME", rig.state, rig.fleets, rig.linear, rig.worker, rig.clock, lambda: DAY, 42, shell).stop_all()
    assert [argv[:2] for argv, _ in shell.calls] == [["claude", "agents"], ["claude", "stop"]]


def test_stop_all_says_when_the_fleet_read_failed(rig: Rig):
    said = rig.engine.stop_all(Shell(row("ACME-1", "working", "busy"), fleet_fails=True))
    assert rig.engine.note().startswith("cannot stop the hands: the fleet read failed")
    assert len(said) == 1 and said[0].startswith("cannot stop the hands: the fleet read failed")


def test_stop_all_returns_every_hand_it_could_not_stop(rig: Rig):
    shell = Shell(row("ACME-1", "idle", "idle"), row("ACME-2", "idle", "idle"), fail=(("claude", "stop"),))
    said = rig.engine.stop_all(shell)
    assert [line.split(":")[0] for line in said] == ["ACME-1", "ACME-2"]


def test_a_second_lock_on_one_folder_is_refused_and_a_released_one_is_free(tmp_path: Path):
    first = engine.lock(tmp_path / "state")
    assert first is not None
    assert engine.lock(tmp_path / "state") is None
    first.close()
    again = engine.lock(tmp_path / "state")
    assert again is not None
    again.close()


def test_a_record_reaches_reconcile_when_the_read_fails(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    rig.ready()
    seen = []
    real = engine.reconcile
    monkeypatch.setattr(engine, "reconcile", lambda facts, store: seen.append(facts.reading) or real(facts, store))

    def down(*args: Any, **kwargs: Any) -> Any:
        raise LinearError("network", "Linear is unreachable")

    monkeypatch.setattr(engine.reading, "read", down)
    asked(rig.store, "s1")
    rig.step()
    feature = seen[-1].feature
    assert asks.QUESTION in [r.kind for r in feature.records["s1"]]
    assert rig.engine.note() == "cannot read: network: Linear is unreachable"


def test_a_later_post_keeps_a_failed_launch_backing_off(clone: Path):
    configure(clone, 2)
    rig = Rig(building())
    rig.ready()
    [launch] = [a for a in rig.worker.submitted if a.name == NAME]
    rig.worker.finish(launch, "ACME-1: cannot fetch origin")
    rig.worker.finish(Post(NAME, "sync:s2:hand-ACME-1", "sync", "body", "s1", None))
    rig.wait(RETRY_AFTER - 10)
    assert [a.name for a in rig.worker.submitted].count(NAME) == 1
    assert rig.engine.note() == "ACME-1: cannot fetch origin"


def test_a_state_read_that_fails_is_the_note_and_the_tick_survives(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    rig.ready()

    def broken() -> None:
        raise Refusal("deckhand.db is locked")

    monkeypatch.setattr(rig.state, "hands", broken)
    rig.step()
    assert rig.engine.note() == "cannot read the state: deckhand.db is locked"
    monkeypatch.undo()
    rig.step()
    assert rig.engine.note() == ""


def test_a_heartbeat_that_fails_is_the_note_until_one_lands(rig: Rig, monkeypatch: pytest.MonkeyPatch):
    def broken(pid: int, at: float) -> None:
        raise Refusal("deckhand.db is locked")

    monkeypatch.setattr(rig.state, "beat", broken)
    rig.engine.tick()
    assert rig.engine.note() == "cannot write the heartbeat: deckhand.db is locked"
    monkeypatch.undo()
    rig.step(5)
    assert rig.engine.note() == ""


def test_a_link_before_the_first_fleet_read_is_good(rig: Rig):
    rig.fleets.value = None
    found = rig.link()
    assert found.error == "" and found.reading is not None


def test_the_snapshot_goes_through_the_engine_state(rig: Rig, tmp_path: Path):
    rig.state.close()
    rig.state = LocalState(tmp_path / "elsewhere.db")
    rig.engine = Engine("ACME", rig.state, rig.fleets, rig.linear, rig.worker, rig.clock, lambda: DAY, 42)
    rig.ready()
    found = status.get(rig.state)
    assert found is not None and found["feature"] == "Alpha"
    assert status.read(answers.state_dir()) is None


def test_a_failed_launch_whose_hand_turns_up_running_clears_its_line(rig: Rig):
    rig.ready()
    [launch] = rig.worker.submitted
    rig.worker.finish(launch, "ACME-1: cannot fetch origin")
    rig.step()
    held_hand(rig)
    rig.clock.t += 0.5
    rig.sessions(row("ACME-1", "working", "busy"))
    rig.step()
    rig.step()
    assert rig.engine.note() == "" and len(rig.worker.submitted) == 1


def fail_every_submitted(rig: Rig, seconds: float, line: str, name: str | None = None) -> list[str]:
    notes, answered = [], 0
    for _ in range(int(seconds // 5)):
        for action in rig.worker.submitted[answered:]:
            rig.worker.finish(action, line if name in (None, action.name) else None)
        answered = len(rig.worker.submitted)
        rig.landed()
        rig.step(5)
        notes.append(rig.engine.note())
    return notes


def test_a_dead_network_at_cap_one_keeps_both_launches_backing_off_and_the_line_shown(rig: Rig):
    rig.ready()
    notes = fail_every_submitted(rig, 1800, "cannot fetch origin")
    launches = [a for a in rig.worker.submitted if isinstance(a, Launch)]
    assert len(launches) <= 14
    assert "" not in notes[1:]


def test_one_failed_fleet_read_keeps_a_failed_launch_backing_off(rig: Rig):
    rig.ready()
    [launch] = rig.worker.submitted
    rig.worker.finish(launch, "ACME-1: cannot fetch origin")
    rig.step()
    rig.fleets.value = fleet.Fleet([], "the fleet read failed: claude vanished")
    rig.landed()
    rig.step()
    rig.sessions()
    rig.wait(30)
    assert rig.engine.note() == "ACME-1: cannot fetch origin"
    assert [a.name for a in rig.worker.submitted].count(NAME) == 1


def test_a_failed_launch_clears_when_its_story_is_finished(rig: Rig):
    rig.ready()
    [launch] = rig.worker.submitted
    rig.worker.finish(launch, "ACME-1: cannot fetch origin")
    rig.step()
    rig.store.set_issue_state("s1", "Done")
    rig.clock.t += 0.5
    rig.link()
    rig.sessions()
    rig.step()
    rig.step()
    assert rig.engine.note() == ""


def test_five_minutes_of_a_failing_rm_make_a_bounded_number_of_calls(clone: Path):
    configure(clone, 1)
    original, copy = two_sessions("stopped")
    rig = Rig(building(), original, copy)
    held_hand(rig)
    rig.ready()
    notes = fail_every_submitted(rig, 300, "ACME-1: claude rm failed: busy")
    removals = [a for a in rig.worker.submitted if isinstance(a, Remove)]
    assert 2 <= len(removals) <= 4
    assert notes[-1] == "ACME-1: claude rm failed: busy"


def test_a_stop_that_keeps_failing_for_a_finished_story_backs_off_and_shows(clone: Path):
    configure(clone, 1)
    rig = Rig(building(), row("ACME-1", "idle", "idle"))
    held_hand(rig)
    started(rig.store, "s1")
    rig.store.set_issue_state("s1", "Done")
    rig.ready()
    notes = fail_every_submitted(rig, 120, "ACME-1: claude stop failed: busy")
    stops = [a for a in rig.worker.submitted if isinstance(a, Stop) and a.name == NAME]
    assert 2 <= len(stops) <= 3
    assert notes[-1] == "ACME-1: claude stop failed: busy"


def test_a_failed_launch_clears_once_its_story_belongs_to_someone_else(rig: Rig):
    rig.ready()
    notes = fail_every_submitted(rig, 10, "ACME-1: cannot fetch origin")
    assert notes[-1].endswith("cannot fetch origin")
    rig.store.assign("s1", "someone-else")
    rig.store.assign("s2", "someone-else")
    rig.link()
    rig.landed()
    rig.step()
    rig.step()
    assert rig.engine.note() == ""


def test_a_failed_stop_clears_once_its_story_is_reopened(clone: Path):
    configure(clone, 1)
    rig = Rig(building(), row("ACME-1", "idle", "idle"))
    held_hand(rig)
    started(rig.store, "s1")
    rig.store.set_issue_state("s1", "Done")
    rig.ready()
    notes = fail_every_submitted(rig, 10, "ACME-1: claude stop failed: busy", NAME)
    assert notes[-1] == "ACME-1: claude stop failed: busy"
    rig.store.set_issue_state("s1", "In Progress")
    rig.link()
    rig.landed()
    rig.step()
    rig.step()
    assert rig.engine.note() == ""


def test_a_failing_resume_for_a_reassigned_story_stays_backed_off(clone: Path):
    configure(clone, 1)
    rig = Rig(building(), row("ACME-1", "stopped"))
    held_hand(rig)
    started(rig.store, "s1")
    rig.state.post("wake:s1:n1", NAME, asks.ANSWER, "Redis")
    rig.store.assign("s1", "someone-else")
    rig.ready()
    notes = fail_every_submitted(rig, 300, "ACME-1: claude --bg failed: busy", NAME)
    resumes = [a for a in rig.worker.submitted if isinstance(a, Resume)]
    assert 2 <= len(resumes) <= 4
    assert notes[-1] == "ACME-1: claude --bg failed: busy"
