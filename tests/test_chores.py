from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from deckhand import asks, chores, features, implplan, queue, signoff
from deckhand.features import FEATURE_LABEL
from deckhand.ops import create_id
from deckhand.records import HAND, Record
from deckhand.steps.after import After
from deckhand.steps.decide import Decide
from deckhand.store.cached import CachedStore
from deckhand.store.fake import FakeStore
from tests.conftest import CountingStore
from tests.test_feature_approval import TEAM, approve_everything, setup
from tests.test_implplan import manifest
from tests.test_step_after import CHORES, statuses
from tests.test_step_build import state
from tests.test_step_review import answer

__all__ = ["state"]


def released(items: list[str] = CHORES) -> FakeStore:
    store = setup()
    approve_everything(store)
    me = store.viewer()["id"]
    start = {"kind": queue.STORY_HAND, "session": "hand-ACME-1"}
    store.put_record("s1", Record(asks.HAND_START, "s1", "", HAND, start))
    store.put_record("s1", Record(implplan.PURPOSE, "s1", "p1", "agent:implplan", manifest(after_merge=items)))
    data = {"answer": "Sign off", "nonce": "n1", "by": me}
    store.put_record("s1", Record(signoff.KIND, "s1", "c1:digest", "person:sess", data))
    store.assign("s1", me)
    for story in ("s1", "s2"):
        store.set_issue_state(story, "Done")
    return store


def opened_next(store: FakeStore) -> str:
    beta = create_id("ACME:Beta", "feature")
    store.create_project(beta, "ACME", "Beta", "", [FEATURE_LABEL])
    store.set_project_dates(beta, "2026-11-23", "2026-11-30")
    store.create_issue("s3", "ACME", "Three", "Outcome.", beta, [])
    return beta


def held(store: FakeStore) -> list[features.Feature]:
    current = features.current(store, "ACME")
    assert current is not None
    return [current, *chores.elsewhere(store, "ACME", {s["id"] for s in current.stories})]


def test_released_items_stay_after_the_next_feature_opens(state: Path, capsys: pytest.CaptureFixture[str]):
    store = released()
    me = store.viewer()["id"]
    After(store).apply(["ACME-1", "person", "2", "--why", "the secret lives in the vault", *TEAM])
    opened_next(store)
    [left] = [r for r in store.records("s1") if r.kind == chores.AFTER_PERSON]
    assert [
        (s["identifier"], n, text, why, at) for s, n, text, why, at in chores.after_merge_queue(held(store), me)
    ] == [("ACME-1", 2, CHORES[1], "the secret lives in the vault", left.at)]
    assert chores.after_merge_queue(held(store), "someone-else") == []
    store.assign("s1", None)
    assert chores.after_merge_queue(held(store), me) == []
    store.assign("s1", me)
    assert statuses(store, capsys) == ["open", "left for the person"]
    answer(state, Decide(store), capsys, ["after-merge", "ACME-1", "--item", "2"], "Done")
    After(store).apply(["ACME-1", "done", "1", "--note", "dropped", *TEAM])
    assert statuses(store, capsys) == ["done by the hand", "done by the person"]
    assert chores.after_merge_queue(held(store), me) == []
    assert [f.project["name"] for f in held(store)] == ["Beta"]


def test_elsewhere_loads_only_a_signed_feature_with_an_item_not_settled(
    counting_store: Callable[[FakeStore], CountingStore],
):
    store = released()
    opened_next(store)
    gamma = create_id("ACME:Gamma", "feature")
    store.create_project(gamma, "ACME", "Gamma", "", [FEATURE_LABEL])
    store.set_project_dates(gamma, "2026-11-02", "2026-11-09")
    store.create_issue("s4", "ACME", "Four", "Outcome.", gamma, [])
    store.put_record("s4", Record(implplan.PURPOSE, "s4", "p1", "agent:implplan", manifest(after_merge=CHORES)))
    store.set_issue_state("s4", "Done")
    alpha = store.issue("s1")["project"]
    counting = counting_store(store)
    found = chores.elsewhere(CachedStore(counting), "ACME", {"s3"})
    assert [f.project["id"] for f in found] == [alpha]
    assert chores.open_in(found[0])
    assert [call[1] for call in counting.log if call[0] == "project_issues"] == [(alpha,)]
    assert [call[1] for call in counting.log if call[0] == "issues"] == [(["s1"],)]
    assert chores.elsewhere(CachedStore(counting), "ACME", {"s1", "s3"}) == []
    for n in (1, 2):
        After(store).apply(["ACME-1", "done", str(n), "--note", "done", *TEAM])
    counting.log.clear()
    assert chores.elsewhere(CachedStore(counting), "ACME", {"s3"}) == []
    assert {call[0] for call in counting.log} <= {"recorded", "records", "team_projects"}
    assert [f.project["id"] for f in chores.elsewhere(store, "ACME", {"s3"}, frozenset({"hand-ACME-1"}))] == [alpha]


def test_a_long_chore_name_is_cut_at_a_word():
    store = released()
    text = "Rotate the webhook signing secret in the vault and update every consumer that reads it now"
    assert len(text) == 90
    story = store.issue("s1")
    id = chores.make_story(store, "ACME", story, 1, text, "the secret lives in the vault")
    name = store.project(id)["name"]
    assert len(name) <= 64 and name.endswith("...")
    assert name == "Chore: Rotate the webhook signing secret in the vault and..."
