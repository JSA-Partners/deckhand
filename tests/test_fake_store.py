from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand.linear.client import DuplicateId
from deckhand.records import Record
from deckhand.ref import Ref
from deckhand.store.fake import FakeStore, WriteFailed


def make_issue(store: FakeStore, id: str = "i1", title: str = "Title") -> str:
    return store.create_issue(id, "ACME", title, "body", None, ["deckhand"])


def approval(version: str) -> Record:
    return Record("approval", "plan", version, "captain", {"ok": True})


def test_create_then_read() -> None:
    store = FakeStore()
    assert make_issue(store) == "i1"
    issue = store.issue("i1")
    assert issue is not None
    assert issue["identifier"] == "ACME-1"
    assert issue["title"] == "Title"
    assert issue["labels"] == ["deckhand"]
    assert store.issue_by_ref(Ref("ACME", 1)) == issue
    assert store.issue_by_ref(Ref("ACME", 2)) is None
    assert store.issue("missing") is None


def test_numbers_count_within_a_team() -> None:
    store = FakeStore()
    make_issue(store, "i1")
    second = store.create_issue("i2", "ACME", "Two", "", None, [])
    other = store.create_issue("i3", "OTHER", "Three", "", None, [])
    assert store.issue(second)["number"] == 2
    assert store.issue(other)["number"] == 1


def test_duplicate_create_raises_duplicate() -> None:
    store = FakeStore()
    make_issue(store)
    with pytest.raises(DuplicateId) as caught:
        make_issue(store)
    assert caught.value.entity == "Issue"
    assert store.issue("i1")["number"] == 1
    assert len(store.writes) == 1


def test_set_labels_replaces() -> None:
    store = FakeStore()
    make_issue(store)
    store.set_labels("i1", ["a", "b"])
    assert store.issue("i1")["labels"] == ["a", "b"]


def test_put_document_creates_then_updates() -> None:
    store = FakeStore()
    store.put_document("d1", "i1", "Plan", "one")
    store.put_document("d1", "i1", "Plan", "two")
    document = store.document("d1")
    assert document is not None
    assert document["body"] == "two"
    assert document["parent"] == "i1"
    assert store.document("nope") is None


def test_put_record_replaces_same_kind_and_subject() -> None:
    store = FakeStore()
    make_issue(store)
    store.put_record("i1", approval("1"))
    store.put_record("i1", approval("2"))
    assert store.records("i1") == [approval("2")]


def test_records_keep_distinct_subjects() -> None:
    store = FakeStore()
    make_issue(store)
    store.put_record("i1", Record("approval", "b", "1", "x", {}))
    store.put_record("i1", Record("approval", "a", "1", "x", {}))
    assert [r.subject for r in store.records("i1")] == ["a", "b"]
    assert store.records("other") == []


def test_relate_and_relations() -> None:
    store = FakeStore()
    store.relate("i1", "i2")
    store.relate("i3", "i1")
    assert store.relations("i1") == [("i1", "i2"), ("i3", "i1")]
    assert store.relations("i2") == [("i1", "i2")]


def test_writes_record_each_call_in_order() -> None:
    store = FakeStore()
    make_issue(store)
    store.set_labels("i1", ["x"])
    store.relate("i1", "i2")
    assert [name for name, *_ in store.writes] == ["create_issue", "set_labels", "relate"]


def test_snapshot_round_trip(tmp_path: Path) -> None:
    store = FakeStore()
    make_issue(store)
    store.put_document("d1", "i1", "Plan", "text")
    store.put_record("i1", approval("1"))
    store.relate("i1", "i2")
    store.create_project("p1", "ACME", "Feature", "about")
    store.set_project_dates("p1", "2026-11-01", None)
    store.set_issue_state("i1", "Done")
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(store.snapshot()))
    loaded = FakeStore.from_snapshot(path)
    assert loaded.snapshot() == store.snapshot()
    assert loaded.writes == []
    assert loaded.records("i1") == [approval("1")]
    assert loaded.project("p1") == store.project("p1")
    assert loaded.create_issue("i2", "ACME", "Next", "", None, []) == "i2"
    assert loaded.issue("i2")["number"] == 2


def test_fail_after_write_lands_then_raises() -> None:
    store = FakeStore()
    store.fail_after_write(1)
    with pytest.raises(WriteFailed):
        make_issue(store)
    assert store.issue("i1") is not None


def test_fail_after_write_counts_from_one() -> None:
    store = FakeStore()
    store.fail_after_write(2)
    make_issue(store)
    with pytest.raises(WriteFailed):
        store.set_labels("i1", ["x"])
    assert store.issue("i1")["labels"] == ["x"]


def test_snapshot_ignores_relation_order() -> None:
    first, second = FakeStore(), FakeStore()
    first.relate("i1", "i2")
    first.relate("i3", "i1")
    second.relate("i3", "i1")
    second.relate("i1", "i2")
    assert first.snapshot() == second.snapshot()
    assert first.relations("i1") == second.relations("i1")


def test_relations_keep_duplicates() -> None:
    store = FakeStore()
    store.relate("i1", "i2")
    store.relate("i1", "i2")
    assert store.relations("i1") == [("i1", "i2"), ("i1", "i2")]


def test_project_round_trip() -> None:
    store = FakeStore()
    assert store.project("p1") is None
    assert store.create_project("p1", "ACME", "Feature", "about") == "p1"
    expected = {
        "id": "p1",
        "name": "Feature",
        "description": "about",
        "start_date": None,
        "target_date": None,
        "labels": [],
    }
    assert store.project("p1") == expected
    store.set_project_dates("p1", "2026-11-01", "2026-12-01")
    assert store.project("p1") == {**expected, "start_date": "2026-11-01", "target_date": "2026-12-01"}
    store.set_project_dates("p1", None, None)
    assert store.project("p1") == expected
    assert [w[0] for w in store.writes] == ["create_project", "set_project_dates", "set_project_dates"]


def test_duplicate_project_raises_duplicate() -> None:
    store = FakeStore()
    store.create_project("p1", "ACME", "Feature", "about")
    with pytest.raises(DuplicateId) as caught:
        store.create_project("p1", "ACME", "Other", "")
    assert caught.value.entity == "Project"
    assert store.project("p1")["name"] == "Feature"
    assert len(store.writes) == 1


def test_team_projects_lists_only_that_team() -> None:
    store = FakeStore()
    store.create_project("p2", "ACME", "Second", "")
    store.create_project("p1", "ACME", "First", "")
    store.create_project("p3", "OTHER", "Third", "")
    assert [p["id"] for p in store.team_projects("ACME")] == ["p1", "p2"]
    assert [p["id"] for p in store.team_projects("OTHER")] == ["p3"]
    assert store.team_projects("NONE") == []


def test_project_issues_lists_members_with_state() -> None:
    store = FakeStore()
    store.create_project("p1", "ACME", "Feature", "")
    store.create_issue("i1", "ACME", "One", "", "p1", [])
    store.create_issue("i2", "ACME", "Two", "", "p1", ["deckhand"])
    store.create_issue("i3", "ACME", "Three", "", None, [])
    store.set_issue_state("i2", "Done")
    members = store.project_issues("p1")
    assert [(m["id"], m["state"]) for m in members] == [("i1", "Backlog"), ("i2", "Done")]
    assert {k: v for k, v in members[1].items() if k != "state"} == store.issue("i2")
    assert "state" not in store.issue("i2")
    assert store.project_issues("none") == []
