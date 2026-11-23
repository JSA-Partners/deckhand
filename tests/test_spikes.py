from __future__ import annotations

from pathlib import Path

from deckhand import features, ops, spikes
from deckhand.features import FEATURE_LABEL
from deckhand.records import content_hash
from deckhand.store.fake import FakeStore

PROJECT = ops.create_id("ACME:Alpha", "feature")


def alpha() -> tuple[FakeStore, features.Feature]:
    store = FakeStore()
    store.create_project(PROJECT, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(PROJECT, "2026-11-16", "2026-11-23")
    return store, features.load(store, store.project(PROJECT))


def test_key_and_answer_id():
    key = spikes.key(PROJECT, "Does the queue keep order?")
    assert key == content_hash(f"{PROJECT}\0Does the queue keep order?")[:12]
    assert spikes.key("other", "Does the queue keep order?") != key
    assert spikes.answer_id(PROJECT, key) == ops.create_id(PROJECT, "spike:" + key)


def test_answered_counts_only_documents_whose_id_matches_their_title():
    store, feature = alpha()
    key = spikes.key(PROJECT, "Does the queue keep order?")
    store.put_document(spikes.answer_id(PROJECT, key), PROJECT, "Spike: Does the queue keep order?", "Yes.")
    store.put_document(ops.create_id(PROJECT, "section:Frame"), PROJECT, "Frame", "x")
    store.put_document("loose", PROJECT, "Spike: Is it fast?", "x")
    assert [d["title"] for d in spikes.answered(store, feature)] == ["Spike: Does the queue keep order?"]
    store.put_document(spikes.answer_id(PROJECT, key), PROJECT, "Spike: Does the queue keep order, renamed?", "Yes.")
    assert spikes.answered(store, feature) == []


def test_keys_are_the_questions_asked_in_this_project(tmp_path: Path):
    here = spikes.key(PROJECT, "Does the queue keep order?")
    there = spikes.key("other", "Is it fast?")
    for key, question in ((here, "Does the queue keep order?"), (there, "Is it fast?")):
        path = spikes.question_path(tmp_path, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(question)
    assert spikes.question_path(tmp_path, here) == tmp_path / "spikes" / f"{here}.md"
    assert spikes.keys(tmp_path, PROJECT) == {here}
    assert spikes.keys(tmp_path / "none", PROJECT) == set()
