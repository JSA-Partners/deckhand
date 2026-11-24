from __future__ import annotations

from deckhand import features, planning, spec, versions
from deckhand.records import Record, content_hash
from deckhand.store.fake import FakeStore
from tests.test_step_decide import feature_store


def item(store: FakeStore, story: str, text: str, data: dict) -> None:
    subject = versions.item_subject(text)
    store.put_record(story, Record("decision-item", subject, content_hash(subject), "person:x", data))


def loaded(store: FakeStore) -> tuple[features.Feature, dict]:
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    return feature, features.story(store, feature, "ACME-1")


def test_gather_reads_the_three_sections_and_the_answered_items():
    store = feature_store()
    feature, _ = loaded(store)
    planning.save_section(store, feature, "Examples", "An order of two.", False)
    planning.save_section(store, feature, "Invariants", "Totals never go negative.", True)
    item(store, "s1", "Ids are UUIDs", {"answer": "Keep", "item": "Ids are UUIDs", "question": "Q?"})
    item(store, "s1", "Refunds wait", {"answer": "Change", "item": "Refunds wait", "question": "Q?"})
    found = spec.gather(store, *loaded(store))
    assert found.contract == f"{planning.WRITTEN}\n\nPOST /orders."
    assert found.examples == f"{planning.WRITTEN}\n\nAn order of two."
    assert found.invariants == f"{planning.DRAFTED}\n\nTotals never go negative."
    assert found.scope == "One\n\nOutcome."
    answered = [
        {"item": "Ids are UUIDs", "answer": "Keep", "note": None},
        {"item": "Refunds wait", "answer": "Change", "note": None},
    ]
    assert found.decisions == sorted(answered, key=lambda d: versions.item_subject(d["item"]))


def test_an_unanswered_decision_item_is_left_out():
    store = feature_store()
    item(store, "s1", "Ids are UUIDs", {"item": "Ids are UUIDs"})
    item(store, "s2", "Refunds wait", {"answer": "Discuss", "item": "Refunds wait"})
    item(store, "s1", "Refunds wait", {"answer": "Discuss", "item": "Refunds wait"})
    assert spec.gather(store, *loaded(store)).decisions == [{"item": "Refunds wait", "answer": "Discuss", "note": None}]


def test_a_missing_section_is_none_and_prints_as_not_written():
    store = feature_store()
    found = spec.gather(store, *loaded(store))
    assert (found.examples, found.invariants, found.decisions) == (None, None, [])
    assert spec.text(found) == (
        "# Contract\n\nWritten by the person.\n\nPOST /orders.\n\n"
        "# Examples\n\nnot written\n\n"
        "# Invariants\n\nnot written\n\n"
        "# Scope\n\nOne\n\nOutcome.\n\n"
        "# Decisions\n\nnot written\n"
    )


def test_decisions_print_as_a_list():
    decisions = [{"item": "Ids", "answer": "Keep", "note": None}, {"item": "X", "answer": "Change", "note": "Y"}]
    found = spec.Spec("C", "E", "I", "One", decisions)
    assert spec.text(found).endswith("# Decisions\n\n- Ids: Keep\n- X: Change (Y)\n")


def test_the_pointer_names_the_build_context_read():
    assert spec.pointer("ACME-1") == (
        "You are the hand of ACME-1. Before anything else run: deckhand build context ACME-1. It prints what"
        " the person approved (Contract, Examples, Invariants, scope, decisions) and your plan."
    )
