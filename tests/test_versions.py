from __future__ import annotations

from deckhand import features, ops, planning, versions
from deckhand.features import FEATURE_LABEL
from deckhand.ops import create_id
from deckhand.records import Record, content_hash
from deckhand.steps import stories
from deckhand.store.fake import FakeStore

P1 = create_id("ACME:Alpha", "feature")


def setup() -> FakeStore:
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
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


def tiered(store: FakeStore, fact: str, chart: str = "") -> None:
    for found in stories.map_records("s1", {"tier": fact, "state_chart": chart}):
        ops.ensure_record(store, "s1", found)


def test_changed_story_voids_its_lean_decision():
    store = setup()
    tiered(store, "none")
    record(store, "lean", "Prove it in Lean")
    lean = next(r for r in store.records("s1") if r.kind == "lean")
    assert versions.current(store, load(store), lean)
    store.set_issue_text("s1", "Orders and refunds", "Outcome: an order is placed.")
    assert not versions.current(store, load(store), lean)


def test_a_new_tier_in_the_map_voids_the_lean_decision():
    store = setup()
    tiered(store, "none")
    record(store, "lean", "Prove it in Lean")
    tiered(store, "data_rule")
    lean = next(r for r in store.records("s1") if r.kind == "lean")
    assert not versions.current(store, load(store), lean)


def test_contract_version_follows_only_its_section():
    store = setup()
    planning.save_section(store, load(store), "Contract", "POST /orders.", False)
    before = versions.version_for(store, load(store), "contract", P1)
    planning.save_section(store, load(store), "Frame", "Why.", False)
    assert versions.version_for(store, load(store), "contract", P1) == before
    planning.save_section(store, load(store), "Contract", "POST /orders and GET /orders.", False)
    assert versions.version_for(store, load(store), "contract", P1) != before


def test_decision_item_version_covers_its_draft():
    text = "Keep orders in one table"
    assert versions.item_version(text, "Yes") == content_hash(text + "\0Yes")
    assert versions.item_version(text, "Yes") != versions.item_version(text, "No")
    assert versions.item_subject(text) == content_hash(text)[:12]


def test_expected_drafts_read_the_stub_objects():
    stub = {"decisions": [{"item": "Greeting", "draft": "Bonjour"}, "stray"]}
    records = [Record("stub", "k", "1", versions.MAP_AUTHOR, stub)]
    assert versions.drafts(records) == {"Greeting": "Bonjour"}


def test_the_same_map_saved_again_keeps_the_lean_decision():
    store = setup()
    tiered(store, "multi_step", "a -> b")
    record(store, "lean", "Prove it in Lean")
    tiered(store, "multi_step", "a -> b")
    lean = next(r for r in store.records("s1") if r.kind == "lean")
    assert versions.current(store, load(store), lean)


def test_editing_the_chart_text_changes_its_version():
    store = setup()
    for record in stories.map_records("s1", {"tier": "multi_step", "state_chart": "a -> b"}):
        ops.ensure_record(store, "s1", record)
    before = versions.feature_version(store, load(store))
    chart = versions.held(store.records("s1"), "state-chart", "s1")
    for record in stories.map_records("s1", {"tier": "multi_step", "state_chart": "a -> b -> c"}):
        ops.ensure_record(store, "s1", record)
    edited = versions.held(store.records("s1"), "state-chart", "s1")
    assert chart is not None and edited is not None
    assert (chart.version, edited.version) == (versions.chart_version("a -> b"), versions.chart_version("a -> b -> c"))
    assert versions.feature_version(store, load(store)) != before


def test_an_edited_after_merge_item_changes_the_feature_version():
    store = setup()
    stub = {"unknowns": [], "new_here": [], "decisions": [], "after_merge": ["rotate the key"]}
    store.put_record("s1", Record("stub", "orders", "1", "agent:stories", stub))
    before = versions.feature_version(store, load(store))
    store.put_record("s1", Record("stub", "orders", "1", "agent:stories", {**stub, "after_merge": ["drop it"]}))
    assert versions.feature_version(store, load(store)) != before
