from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import implplan, signoff
from deckhand.features import FEATURE_LABEL
from deckhand.ops import create_id
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.after import After
from deckhand.steps.decide import Decide
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import TEAM, approve_everything, setup
from tests.test_implplan import manifest
from tests.test_step_review import answer, state

__all__ = ["state"]

CHORES = ["drop the old queue table", "rotate the webhook secret"]


def chored() -> FakeStore:
    store = setup()
    approve_everything(store)
    plan = Record(implplan.PURPOSE, "s1", "p1", "agent:implplan", manifest(after_merge=CHORES))
    store.put_record("s1", plan)
    return store


def listing(store: FakeStore, capsys: pytest.CaptureFixture[str]) -> dict:
    capsys.readouterr()
    After(store).context(["ACME-1", *TEAM, "--json"])
    return json.loads(capsys.readouterr().out)


def statuses(store: FakeStore, capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [item["status"] for item in listing(store, capsys)["items"]]


def puts(store: FakeStore) -> list:
    return [w for w in store.writes if w[0] == "put_record"]


def test_after_items_open_once_done(capsys: pytest.CaptureFixture[str]):
    store = chored()
    before = listing(store, capsys)
    assert (before["done"], [i["text"] for i in before["items"]]) == (False, CHORES)
    assert {i["status"] for i in before["items"]} == {"waits for the merge"}
    written = list(store.writes)
    with pytest.raises(Refusal, match="ACME-1 is not Done"):
        After(store).apply(["ACME-1", "done", "1", "--note", "dropped", *TEAM])
    assert store.writes == written
    store.set_issue_state("s1", "Done")
    after = listing(store, capsys)
    assert after["done"] is True
    assert [(i["n"], i["status"]) for i in after["items"]] == [(1, "open"), (2, "open")]


def test_hand_ticks_an_item(capsys: pytest.CaptureFixture[str]):
    store = chored()
    store.set_issue_state("s1", "Done")
    After(store).apply(["ACME-1", "done", "1", "--note", "dropped in migration 12", *TEAM])
    [record] = [r for r in store.records("s1") if r.kind == signoff.AFTER_DONE]
    assert (record.subject, record.data["note"]) == ("s1:1", "dropped in migration 12")
    assert statuses(store, capsys) == ["done by the hand", "open"]
    written = list(store.writes)
    with pytest.raises(Refusal, match="item 1 of ACME-1 is done by the hand"):
        After(store).apply(["ACME-1", "done", "1", "--note", "again", *TEAM])
    with pytest.raises(Refusal, match="no item 3"):
        After(store).apply(["ACME-1", "done", "3", "--note", "x", *TEAM])
    with pytest.raises(Refusal, match="--note"):
        After(store).apply(["ACME-1", "done", "2", *TEAM])
    store.assign("s1", "someone-else")
    with pytest.raises(Refusal, match="ACME-1 belongs to someone-else"):
        After(store).apply(["ACME-1", "done", "2", "--note", "rotated", *TEAM])
    assert puts(store) == [w for w in written if w[0] == "put_record"]
    store.assign("s1", store.viewer()["id"])
    store.set_issue_state("s2", "Done")
    After(store).apply(["ACME-1", "done", "2", "--note", "rotated", *TEAM])
    assert statuses(store, capsys) == ["done by the hand", "done by the hand"]


def test_item_left_for_person_is_asked(state: Path, capsys: pytest.CaptureFixture[str]):
    store = chored()
    store.set_issue_state("s1", "Done")
    store.set_issue_state("s2", "Done")
    After(store).apply(["ACME-1", "person", "2", "--why", "the secret lives in the vault", *TEAM])
    records = store.records("s1")
    assert signoff.after_merge_for_person(records, "s1") == [(2, CHORES[1], "the secret lives in the vault")]
    assert statuses(store, capsys) == ["open", "left for the person"]
    question = answer(state, Decide(store), capsys, ["after-merge", "ACME-1", "--item", "2"], "Not yet")
    assert [o["label"] for o in question["options"]] == ["Done", "Not yet"]
    assert CHORES[1] in question["question"]
    assert signoff.after_merge_for_person(store.records("s1"), "s1") != []
    answer(state, Decide(store), capsys, ["after-merge", "ACME-1", "--item", "2"], "Done")
    [decided] = [r for r in store.records("s1") if r.kind == signoff.AFTER]
    assert (decided.subject, decided.data["answer"]) == ("s1:2", "Done")
    assert signoff.after_merge_for_person(store.records("s1"), "s1") == []
    assert statuses(store, capsys) == ["open", "done by the person"]


def test_after_merge_decision_refused_for_hand_item(capsys: pytest.CaptureFixture[str]):
    store = chored()
    store.set_issue_state("s1", "Done")
    After(store).apply(["ACME-1", "done", "1", "--note", "dropped", *TEAM])
    written = list(store.writes)
    for item in ("1", "2"):
        with pytest.raises(Refusal, match=f"item {item} of ACME-1 is not left for the person"):
            Decide(store).context(["after-merge", "ACME-1", "--item", item, *TEAM])
    assert store.writes == written


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
    waiting = signoff.after_merge_queue(store, "ACME", me)
    assert [(s["identifier"], n, text, why) for s, n, text, why in waiting] == [
        ("ACME-1", 2, CHORES[1], "the secret lives in the vault")
    ]
    assert signoff.after_merge_queue(store, "ACME", "someone-else") == []
    assert statuses(store, capsys) == ["open", "left for the person"]
    answer(state, Decide(store), capsys, ["after-merge", "ACME-1", "--item", "2"], "Done")
    After(store).apply(["ACME-1", "done", "1", "--note", "dropped", *TEAM])
    assert statuses(store, capsys) == ["done by the hand", "done by the person"]
    assert signoff.after_merge_queue(store, "ACME", me) == []
