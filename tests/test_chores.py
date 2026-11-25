from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from deckhand import chores
from deckhand.features import FEATURE_LABEL
from deckhand.ops import create_id
from deckhand.steps.after import After
from deckhand.steps.decide import Decide
from deckhand.store.cached import CachedStore
from deckhand.store.fake import FakeStore
from tests.conftest import CountingStore
from tests.test_feature_approval import TEAM
from tests.test_step_after import CHORES, chored, statuses
from tests.test_step_build import state
from tests.test_step_review import answer

__all__ = ["state"]


def test_released_items_stay_after_the_next_feature_opens(state: Path, capsys: pytest.CaptureFixture[str]):
    store = chored()
    me = store.viewer()["id"]
    store.assign("s1", me)
    for story in ("s1", "s2"):
        store.set_issue_state(story, "Done")
    After(store).apply(["ACME-1", "person", "2", "--why", "the secret lives in the vault", *TEAM])
    beta = create_id("ACME:Beta", "feature")
    store.create_project(beta, "ACME", "Beta", "", [FEATURE_LABEL])
    store.set_project_dates(beta, "2026-11-23", "2026-11-30")
    store.create_issue("s3", "ACME", "Three", "Outcome.", beta, [])
    waiting = chores.after_merge_queue(store, "ACME", me)
    [left] = [r for r in store.records("s1") if r.kind == chores.AFTER_PERSON]
    assert [(s["identifier"], n, text, why, at) for s, n, text, why, at in waiting] == [
        ("ACME-1", 2, CHORES[1], "the secret lives in the vault", left.at)
    ]
    assert chores.after_merge_queue(store, "ACME", "someone-else") == []
    store.assign("s1", None)
    assert chores.after_merge_queue(store, "ACME", me) == []
    store.assign("s1", me)
    assert statuses(store, capsys) == ["open", "left for the person"]
    answer(state, Decide(store), capsys, ["after-merge", "ACME-1", "--item", "2"], "Done")
    After(store).apply(["ACME-1", "done", "1", "--note", "dropped", *TEAM])
    assert statuses(store, capsys) == ["done by the hand", "done by the person"]
    assert chores.after_merge_queue(store, "ACME", me) == []


def test_the_after_merge_queue_loads_a_released_feature_only_for_an_item_left_in_the_records(
    counting_store: Callable[[FakeStore], CountingStore],
):
    store = chored()
    me = store.viewer()["id"]
    store.assign("s1", me)
    for story in ("s1", "s2"):
        store.set_issue_state(story, "Done")
    beta = create_id("ACME:Beta", "feature")
    store.create_project(beta, "ACME", "Beta", "", [FEATURE_LABEL])
    store.set_project_dates(beta, "2026-11-23", "2026-11-30")
    store.create_issue("s3", "ACME", "Three", "Outcome.", beta, [])
    gamma = create_id("ACME:Gamma", "feature")
    store.create_project(gamma, "ACME", "Gamma", "", [FEATURE_LABEL])
    store.set_project_dates(gamma, "2026-11-02", "2026-11-09")
    store.create_issue("s4", "ACME", "Four", "Outcome.", gamma, [])
    store.set_issue_state("s4", "Done")
    alpha = store.issue("s1")["project"]
    counting = counting_store(store)
    assert chores.after_merge_queue(CachedStore(counting), "ACME", me) == []
    assert [call[1] for call in counting.log if call[0] == "project_issues"] == [(beta,)]
    After(store).apply(["ACME-1", "person", "2", "--why", "the secret lives in the vault", *TEAM])
    counting.log.clear()
    assert len(chores.after_merge_queue(CachedStore(counting), "ACME", me)) == 1
    assert sorted(call[1] for call in counting.log if call[0] == "project_issues") == sorted([(beta,), (alpha,)])
