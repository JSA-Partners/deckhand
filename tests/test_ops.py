from __future__ import annotations

import uuid
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from deckhand.linear.client import CONFLICT_PREFIX, DuplicateId
from deckhand.ops import (
    create_id,
    ensure_document,
    ensure_issue,
    ensure_labels,
    ensure_record,
    ensure_relation,
)
from deckhand.records import Record
from deckhand.store.fake import FakeStore, WriteFailed

FIELDS: dict[str, Any] = {
    "team": "ACME",
    "title": "Title",
    "description": "body",
    "project": None,
    "labels": ["deckhand"],
}


def test_create_id_is_deterministic_and_distinct() -> None:
    assert create_id("story-1", "issue") == create_id("story-1", "issue")
    assert create_id("story-1", "issue") != create_id("story-1", "plan")
    assert create_id("story-1", "issue") != create_id("story-2", "issue")


def test_create_id_is_a_version_4_uuid() -> None:
    parsed = uuid.UUID(create_id("story-1", "issue"))
    assert parsed.version == 4
    assert parsed.variant == uuid.RFC_4122
    assert str(parsed) == create_id("story-1", "issue")


def test_ensure_issue_twice_writes_once() -> None:
    store = FakeStore()
    first = ensure_issue(store, "story-1", "issue", **FIELDS)
    second = ensure_issue(store, "story-1", "issue", **FIELDS)
    assert first == second == create_id("story-1", "issue")
    assert len(store.writes) == 1


def test_concurrent_duplicate_counts_as_success() -> None:
    class Racing(FakeStore):
        def issue(self, id: str) -> dict | None:
            return None

        def create_issue(self, *args: Any, **kwargs: Any) -> str:
            raise DuplicateId("INPUT_ERROR", f"{CONFLICT_PREFIX} Issue")

    assert ensure_issue(Racing(), "story-1", "issue", **FIELDS) == create_id("story-1", "issue")


def test_ensure_labels_writes_only_on_difference() -> None:
    store = FakeStore()
    issue = ensure_issue(store, "story-1", "issue", **FIELDS)
    ensure_labels(store, issue, ["deckhand"])
    assert len(store.writes) == 1
    ensure_labels(store, issue, ["deckhand", "review"])
    assert len(store.writes) == 2
    ensure_labels(store, issue, ["review", "deckhand"])
    assert len(store.writes) == 2


def test_ensure_document_writes_only_on_difference() -> None:
    store = FakeStore()
    issue = ensure_issue(store, "story-1", "issue", **FIELDS)
    doc = ensure_document(store, "story-1", "plan", issue, "Plan", "one")
    assert ensure_document(store, "story-1", "plan", issue, "Plan", "one") == doc
    assert len(store.writes) == 2
    ensure_document(store, "story-1", "plan", issue, "Plan", "two")
    assert len(store.writes) == 3
    assert store.document(doc)["body"] == "two"


def test_ensure_record_writes_only_on_difference() -> None:
    store = FakeStore()
    issue = ensure_issue(store, "story-1", "issue", **FIELDS)
    record = Record("approval", "plan", "1", "captain", {"ok": True})
    ensure_record(store, issue, record)
    ensure_record(store, issue, record)
    assert len(store.writes) == 2
    ensure_record(store, issue, Record("approval", "plan", "2", "captain", {"ok": True}))
    assert len(store.writes) == 3


def test_ensure_relation_writes_the_edge_once() -> None:
    store = FakeStore()
    a = ensure_issue(store, "a", "issue", **FIELDS)
    b = ensure_issue(store, "b", "issue", **FIELDS)
    ensure_relation(store, a, b)
    ensure_relation(store, a, b)
    ensure_relation(store, b, a)
    assert store.relations(a) == sorted([(a, b), (b, a)])


KEYS = ["one", "two", "three"]
LABELS = ["deckhand", "review", "blocked"]

operation = st.one_of(
    st.tuples(st.just("issue"), st.sampled_from(KEYS)),
    st.tuples(
        st.just("labels"),
        st.sampled_from(KEYS),
        st.lists(st.sampled_from(LABELS), unique=True, max_size=3),
    ),
    st.tuples(st.just("document"), st.sampled_from(KEYS), st.sampled_from(["x", "y"]), st.text(max_size=3)),
    st.tuples(st.just("record"), st.sampled_from(KEYS), st.sampled_from(["1", "2"])),
    st.tuples(st.just("relation"), st.sampled_from(KEYS), st.sampled_from(KEYS)),
)


def run(store: FakeStore, operations: list[tuple]) -> None:
    for key in KEYS:
        ensure_issue(store, key, "issue", **FIELDS)
    for name, key, *rest in operations:
        issue = create_id(key, "issue")
        if name == "issue":
            ensure_issue(store, key, "issue", **FIELDS)
        elif name == "labels":
            ensure_labels(store, issue, rest[0])
        elif name == "document":
            ensure_document(store, key, "doc", issue, rest[0], rest[1])
        elif name == "record":
            ensure_record(store, issue, Record("approval", "plan", rest[0], "captain", {}))
        elif name == "relation":
            ensure_relation(store, issue, create_id(rest[0], "issue"))


@settings(max_examples=200, deadline=None, derandomize=True)
@given(operations=st.lists(operation, max_size=8), k=st.integers(min_value=1, max_value=30))
def test_rerun_converges(operations: list[tuple], k: int) -> None:
    clean = FakeStore()
    run(clean, operations)

    broken = FakeStore()
    broken.fail_after_write(k)
    try:
        run(broken, operations)
    except WriteFailed:
        pass
    run(broken, operations)

    assert broken.snapshot() == clean.snapshot()
    relations = broken.snapshot()["relations"]
    assert len(relations) == len({tuple(edge) for edge in relations})
