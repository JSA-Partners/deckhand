from __future__ import annotations

from typing import Any

import pytest

from deckhand import features, planning
from deckhand.ops import create_id
from deckhand.step import Refusal
from deckhand.store.fake import FakeStore

P1 = create_id("ACME:Alpha", "feature")


def open_feature(store: FakeStore) -> features.Feature:
    store.create_project(P1, "ACME", "Alpha", "", [features.FEATURE_LABEL])
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


class CountingStore:
    def __init__(self, inner: FakeStore) -> None:
        self._inner = inner
        self.calls: dict[str, int] = {}

    def __getattr__(self, name: str):
        method = getattr(self._inner, name)

        def counted(*args, **kwargs):
            self.calls[name] = self.calls.get(name, 0) + 1
            return method(*args, **kwargs)

        return counted


def test_all_seven_sections_cost_one_document_read():
    from deckhand import versions
    from deckhand.store.cached import CachedStore

    fake = FakeStore()
    feature = open_feature(fake)
    for name in planning.SECTIONS:
        planning.save_section(fake, feature, name, f"{name} text.", drafted=False)
    counting: Any = CountingStore(fake)
    versions.feature_version(CachedStore(counting), feature)
    assert counting.calls == {"project_documents": 1}


def test_a_missing_section_is_still_none():
    store = FakeStore()
    feature = open_feature(store)
    planning.save_section(store, feature, "Frame", "Text.", drafted=False)
    assert planning.section(store, feature, "Shape") is None
