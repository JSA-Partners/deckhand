from __future__ import annotations

from deckhand import features, ops, planning, versions
from deckhand.ops import create_id
from deckhand.records import Record, content_hash
from deckhand.store.fake import FakeStore
from deckhand.tiers import TIERS

P1 = create_id("ACME:Alpha", "feature")


def setup() -> FakeStore:
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "")
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    store.create_issue("s1", "ACME", "Orders", "Outcome: an order is placed.", P1, [])
    return store


def load(store: FakeStore) -> features.Feature:
    found = features.open_feature(store, "ACME")
    assert found is not None
    return found


def record(store: FakeStore, kind: str, answer: str, **data: object) -> None:
    version = versions.version_for(store, load(store), kind, "s1")
    ops.ensure_record(store, "s1", Record(kind, "s1", version, "person:s", {"answer": answer, **data}))


def tiered(store: FakeStore, index: int) -> None:
    tier = TIERS[index]
    record(store, "tier", tier.label, level=tier.level, fact=tier.fact)


def test_tier_version_depends_only_on_the_story_text():
    store = setup()
    before = versions.version_for(store, load(store), "tier", "s1")
    tiered(store, 0)
    assert versions.version_for(store, load(store), "tier", "s1") == before
    store.set_issue_text("s1", "Orders", "Outcome: an order is placed twice.")
    assert versions.version_for(store, load(store), "tier", "s1") != before


def test_changed_story_voids_its_sitting():
    store = setup()
    tiered(store, 0)
    record(store, "sitting", "Done")
    assert versions.current(store, load(store), store.records("s1")[0])
    store.set_issue_text("s1", "Orders and refunds", "Outcome: an order is placed.")
    feature = load(store)
    sitting = next(r for r in store.records("s1") if r.kind == "sitting")
    assert not versions.current(store, feature, sitting)
    assert "ACME-1: the sitting changed after it was done" in features.approval_gaps(store, feature)


def test_new_tier_answer_voids_the_sitting():
    store = setup()
    tiered(store, 0)
    record(store, "sitting", "Done")
    tiered(store, 2)
    sitting = next(r for r in store.records("s1") if r.kind == "sitting")
    assert not versions.current(store, load(store), sitting)


def test_contract_version_follows_only_its_section():
    store = setup()
    planning.save_section(store, load(store), "Contract", "POST /orders.", False)
    before = versions.version_for(store, load(store), "contract", P1)
    planning.save_section(store, load(store), "Frame", "Why.", False)
    assert versions.version_for(store, load(store), "contract", P1) == before
    planning.save_section(store, load(store), "Contract", "POST /orders and GET /orders.", False)
    assert versions.version_for(store, load(store), "contract", P1) != before


def test_decision_item_version_is_its_text_hash():
    store = setup()
    text = "Keep orders in one table"
    assert versions.version_for(store, load(store), "decision-item", text) == content_hash(text)
    assert versions.item_subject(text) == content_hash(text)[:12]


def test_a_tier_record_of_the_same_fact_keeps_the_sitting():
    store = setup()
    tiered(store, 1)
    record(store, "sitting", "Done")
    record(store, "tier", "reworded", level=TIERS[1].level, fact=TIERS[1].fact)
    sitting = next(r for r in store.records("s1") if r.kind == "sitting")
    assert versions.current(store, load(store), sitting)
