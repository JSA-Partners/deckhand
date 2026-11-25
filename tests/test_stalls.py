from __future__ import annotations

from pathlib import Path

import pytest

from deckhand import asks, features, queue, stalls
from deckhand.fleet import Fleet
from deckhand.policy import Policy
from deckhand.records import HAND, Record, iso
from deckhand.store.fake import FakeStore
from tests.test_queue import ME, alpha, asked, hand, kinds, started, waiting
from tests.test_step_build import repo, state
from tests.test_step_signoff import ready, sign

__all__ = ["repo", "state"]


def test_failed_hand_is_stalled_item():
    store = alpha()
    started(store, "s1")
    [start] = [r for r in store.records("s1") if r.kind == asks.HAND_START]
    for session in (hand("ACME-1", "failed", None), hand("ACME-1", "stopped", None)):
        [item] = waiting(store, sessions=(session,))
        assert (item.kind, item.rank, item.at, item.next) == ("stalled", 2, start.at, ["decide context stalled ACME-1"])
    assert waiting(store, sessions=(hand("ACME-1", "working", "busy"),)) == []
    assert waiting(store, sessions=(hand("ACME-1", "failed", None),), note="the fleet read failed") == []
    store.put_record("s1", Record("checks", "s1", "c2", HAND, {"passed": False}))
    assert waiting(store, sessions=(hand("ACME-1", "failed", None),)) == []
    story = store.issue("s1") | {"state": "In Progress"}
    records = store.records("s1")
    assert stalls.stalled(story, records, None, "story") and stalls.auto_restart(records, "story", None)
    started(store, "s1")
    records = store.records("s1")
    assert not (stalls.stalled(story, records, None, "story") and stalls.auto_restart(records, "story", None))


def stalled_items(store: FakeStore, restarts: dict[str, int] | None = None) -> list[queue.Item]:
    feature = features.open_feature(store, "ACME")
    gone = Fleet([hand("ACME-1", "failed", None)])
    found = queue.items(store, "ACME", feature, ME, gone, lambda story: Policy("widgets"), restarts=restarts)
    return [i for i in found if i.kind == stalls.STALLED]


def stop_answered(store: FakeStore, answer: str) -> None:
    start = queue.last_start(store.records("s1"), "story")
    assert start is not None
    store.put_record("s1", Record(stalls.STALLED, "s1", start.at, "person:session-1", {"answer": answer}))


def test_an_idle_hand_on_its_own_prompt_is_a_prompt_and_never_stalled():
    store = alpha()
    started(store, "s1")
    idle = hand("ACME-1", "idle", "idle", "input needed", iso(1795000000500))
    assert kinds(waiting(store, sessions=(idle,))) == [("prompt", 2, "ACME-1")]
    assert not stalls.stalled(store.issue("s1") | {"state": "In Progress"}, store.records("s1"), idle, "story")


def test_a_first_stop_restarts_automatically_and_asks_nothing():
    store = alpha()
    started(store, "s1")
    story, records = store.issue("s1") | {"state": "In Progress"}, store.records("s1")
    assert stalls.stalled(story, records, None, "story") and stalls.auto_restart(records, "story", 0)
    assert stalled_items(store, {"ACME-1": 0}) == []


def test_a_second_stop_without_progress_asks():
    store = alpha()
    started(store, "s1")
    assert not stalls.auto_restart(store.records("s1"), "story", 1)
    [item] = stalled_items(store, {"ACME-1": 1})
    assert item.next == ["decide context stalled ACME-1"]


def test_a_stop_after_progress_restarts_automatically_again():
    store = alpha()
    started(store, "s1")
    store.put_record("s1", Record("checks", "s1", "c2", HAND, {"passed": False}))
    assert stalls.auto_restart(store.records("s1"), "story", 1)
    assert stalled_items(store, {"ACME-1": 1}) == []


def test_a_hand_with_no_row_restarts_only_after_a_new_record():
    store = alpha()
    started(store, "s1")
    assert not stalls.auto_restart(store.records("s1"), "story", None)
    assert [i.ref for i in stalled_items(store)] == ["ACME-1"]
    assert [i.ref for i in stalled_items(store, {"ACME-2": 0})] == ["ACME-1"]
    store.put_record("s1", Record("checks", "s1", "c2", HAND, {"passed": False}))
    assert stalls.auto_restart(store.records("s1"), "story", None)
    assert stalled_items(store) == []


def test_an_answered_stop_clears_the_item_and_is_no_progress():
    for answer in (stalls.RESTART, stalls.LEAVE):
        store = alpha()
        started(store, "s1")
        stop_answered(store, answer)
        assert stalled_items(store, {"ACME-1": 1}) == []
        assert not stalls.auto_restart(store.records("s1"), "story", 1)
        assert stalls.stall_answer(store.records("s1"), "story") == answer


def test_a_later_start_that_stalls_again_asks_again():
    store = alpha()
    started(store, "s1")
    stop_answered(store, stalls.RESTART)
    assert stalled_items(store, {"ACME-1": 1}) == []
    started(store, "s1")
    assert stalls.stall_answer(store.records("s1"), "story") is None
    [item] = stalled_items(store, {"ACME-1": 1})
    start = queue.last_start(store.records("s1"), "story")
    assert start is not None and item.at == start.at


def test_finished_hand_is_not_stalled():
    store = alpha(planning=True)
    started(store, "s1", "draft")
    records = store.records("s1")
    assert stalls.stalled(store.issue("s1") | {"state": "Backlog"}, records, None, "draft")
    store.put_record("s1", Record(queue.DRAFT, "s1", "d1", HAND, {}))
    assert not stalls.stalled(store.issue("s1") | {"state": "Backlog"}, store.records("s1"), None, "draft")
    done = alpha()
    started(done, "s2")
    assert not stalls.stalled(done.issue("s2") | {"state": "Done"}, done.records("s2"), None, "story")
    asked(done, "s2")
    assert not stalls.stalled(done.issue("s2") | {"state": "Backlog"}, done.records("s2"), None, "story")


def test_a_draft_start_is_no_start_of_a_build_hand():
    store = alpha()
    started(store, "s1", "draft")
    store.put_record("s1", Record(queue.DRAFT, "s1", "d1", HAND, {}))
    story = store.issue("s1") | {"state": "Backlog"}
    assert queue.last_start(store.records("s1"), "story") is None
    assert not stalls.stalled(story, store.records("s1"), None, "story")
    assert waiting(store, sessions=(hand("ACME-1", "done", None),)) == []


def test_second_signoff_due_is_waiting_not_stalled(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    store.put_record("s1", Record(asks.WAKE, "s1", "w1", HAND, {}))
    started(store, "s1")
    failed = (hand("ACME-1", "failed", None),)
    assert [i.kind for i in waiting(store, sessions=failed, second=True)] == []
    assert [i.kind for i in waiting(store, sessions=failed)] == ["stalled"]
    story, records = store.issue("s1") | {"state": "In Review"}, store.records("s1")
    store.put_record("s1", Record("checks", "s1", "x", "agent:checks", {"head": "x", "passed": False}))
    later = store.records("s1")
    assert not stalls.stalled(story, records, None, "story", second=True)
    assert not (stalls.stalled(story, later, None, "story", second=True) and stalls.auto_restart(later, "story", None))
    assert stalls.stalled(story, later, None, "story") and stalls.auto_restart(later, "story", None)


def idle_items(store: FakeStore, idle: frozenset[str], restarts: dict[str, int]) -> list[queue.Item]:
    feature = features.open_feature(store, "ACME")
    blocked = Fleet([hand("ACME-1", "blocked", "busy", None, iso(1795000000500))])
    found = queue.items(
        store, "ACME", feature, ME, blocked, lambda story: Policy("widgets"), restarts=restarts, idle=idle
    )
    return [i for i in found if i.kind == stalls.STALLED]


def test_an_idle_hand_past_its_one_restart_is_a_stalled_item():
    store = alpha()
    started(store, "s1")
    [item] = idle_items(store, frozenset({"ACME-1"}), {"ACME-1": 1})
    assert item.lines[1:] == ["its hand is idle, and no record is new since it last started"]
    assert idle_items(store, frozenset(), {"ACME-1": 1}) == []
    assert idle_items(store, frozenset({"ACME-1"}), {"ACME-1": 0}) == []


def test_an_idle_hand_waiting_on_the_person_is_not_stalled():
    store = alpha()
    started(store, "s1")
    asked(store, "s1")
    assert idle_items(store, frozenset({"ACME-1"}), {"ACME-1": 1}) == []
