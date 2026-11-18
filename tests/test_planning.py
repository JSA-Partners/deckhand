from __future__ import annotations

import pytest

from deckhand import features, planning
from deckhand.ops import create_id
from deckhand.step import Refusal
from deckhand.store.fake import FakeStore

P1 = create_id("ACME:Alpha", "feature")


def open_feature(store: FakeStore) -> features.Feature:
    store.create_project(P1, "ACME", "Alpha", "")
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    found = features.open_feature(store, "ACME")
    assert found is not None
    return found


def test_section_records_who_wrote_it():
    store = FakeStore()
    feature = open_feature(store)
    planning.save_section(store, feature, "Frame", "The problem.", drafted=False)
    planning.save_section(store, feature, "Shape", "The shape.", drafted=True)
    assert planning.section(store, feature, "Frame") == "Written by the person.\n\nThe problem."
    shape = planning.section(store, feature, "Shape")
    assert shape is not None
    assert shape.splitlines()[0] == "Drafted by an agent, accepted by the person."
    assert planning.section(store, feature, "Facts") is None


def test_saving_a_section_twice_writes_once():
    store = FakeStore()
    feature = open_feature(store)
    planning.save_section(store, feature, "Frame", "Text.", drafted=False)
    store.writes.clear()
    planning.save_section(store, feature, "Frame", "Text.", drafted=False)
    assert store.writes == []


def test_unknown_section_is_refused():
    store = FakeStore()
    feature = open_feature(store)
    store.writes.clear()
    with pytest.raises(Refusal, match="Nonsense"):
        planning.save_section(store, feature, "Nonsense", "x", drafted=False)
    with pytest.raises(Refusal, match="empty"):
        planning.save_section(store, feature, "Frame", "  \n", drafted=False)
    assert store.writes == []


def test_next_section_follows_order():
    store = FakeStore()
    feature = open_feature(store)
    assert planning.next_section(store, feature) == "Frame"
    planning.save_section(store, feature, "Shape", "s", drafted=False)
    assert planning.next_section(store, feature) == "Frame"
    planning.save_section(store, feature, "Frame", "f", drafted=False)
    assert planning.next_section(store, feature) == "Examples"
    for name in planning.SECTIONS:
        planning.save_section(store, feature, name, "x", drafted=False)
    assert planning.next_section(store, feature) == "story map"
    store.create_issue("i1", "ACME", "t", "", P1, [])
    refreshed = features.open_feature(store, "ACME")
    assert refreshed is not None
    assert planning.next_section(store, refreshed) is None
