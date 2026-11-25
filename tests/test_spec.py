from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from deckhand import features, launch, planning, spec, versions
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
        "# Decisions\n\nnot written\n\n"
        "# Examples\n\nnot written\n\n"
        "# Invariants\n\nnot written\n\n"
        "# Scope\n\nOne\n\nOutcome.\n"
    )


def test_decisions_print_as_a_list():
    decisions = [{"item": "Ids", "answer": "Keep", "note": None}, {"item": "X", "answer": "Change", "note": "Y"}]
    found = spec.Spec("C", "E", "I", "One", decisions)
    assert "# Decisions\n\n- Ids: Keep\n- X: Change (Y)\n\n# Examples" in spec.text(found)


def test_the_pointer_names_the_build_context_read():
    assert spec.pointer("ACME-1") == (
        "You are the hand of ACME-1. What the person approved (Contract, Examples, Invariants, scope, decisions)"
        " and your plan are in deckhand build context ACME-1; run it unless it is already above."
    )


def test_the_draft_pointer_names_the_draft_context_read():
    assert spec.draft_pointer("ACME-1") == (
        "You are the draft hand of ACME-1. The story's stub, the feature's Frame, Shape, Contract and Examples,"
        " and what the draft holds are in deckhand draft context ACME-1; run it unless it is already above."
    )


def test_planning_text_prints_each_named_section_or_not_written():
    store = feature_store()
    feature, _ = loaded(store)
    assert spec.planning_text(store, feature, ("Frame", "Contract")) == (
        "# Frame\n\nnot written\n\n# Contract\n\nWritten by the person.\n\nPOST /orders.\n"
    )


@pytest.fixture
def temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path / launch.INPUT


def size(text: str) -> int:
    return len(text.encode("utf-8"))


def test_a_context_under_the_bound_prints_whole_and_saves_nothing(temp: Path):
    assert spec.fitted("ACME-1-build", "story: ACME-1\n", "plan:\nnone saved\n") == "story: ACME-1\nplan:\nnone saved\n"
    assert not temp.exists()


def test_a_long_context_prints_what_was_approved_and_the_saved_whole(temp: Path):
    first, rest = "story: ACME-1\n# Contract\n\nPOST /orders.\n", "plan:\n" + "Step é.\n" * 3000
    shown = spec.fitted("ACME-1-build", first, rest)
    saved = temp / "ACME-1-build.md"
    assert size(shown) < spec.LIMIT and shown.startswith(first) and str(saved) in shown
    assert saved.read_text(encoding="utf-8") == first + rest


def test_an_approved_spec_over_the_bound_is_cut_and_still_names_the_whole(temp: Path):
    first = "story: ACME-1\n# Contract\n\n" + "A rule é.\n" * 4000
    shown = spec.fitted("ACME-1-build", first, "plan:\nnone saved\n")
    saved = temp / "ACME-1-build.md"
    assert size(shown) < spec.LIMIT and shown.startswith("story: ACME-1\n# Contract\n\nA rule") and str(saved) in shown
    assert saved.read_text(encoding="utf-8") == first + "plan:\nnone saved\n"


def test_a_planted_link_is_replaced_never_followed(temp: Path, tmp_path: Path):
    temp.mkdir(mode=0o700)
    target = tmp_path / "elsewhere.txt"
    target.write_text("kept")
    (temp / "ACME-1-build.md").symlink_to(target)
    spec.fitted("ACME-1-build", "story: ACME-1\n", "x\n" * 20000)
    assert target.read_text() == "kept"
    assert not (temp / "ACME-1-build.md").is_symlink()


def test_a_context_that_cannot_be_saved_still_prints_under_the_bound(temp: Path, monkeypatch: pytest.MonkeyPatch):
    def refused() -> Path:
        raise OSError("read-only")

    monkeypatch.setattr(launch, "input_folder", refused)
    shown = spec.fitted("ACME-1-build", "story: ACME-1\n", "x\n" * 20000)
    assert size(shown) < spec.LIMIT and shown.startswith("story: ACME-1\n") and "could not be saved" in shown


def test_a_first_line_over_the_bound_keeps_its_start(temp: Path):
    first = "\u00e9" * 15000 + "\n"
    shown = spec.fitted("ACME-1-build", first, "rest\n")
    assert size(shown) < spec.LIMIT and shown.startswith("\u00e9" * 100) and spec.CUT in shown


def test_plain_undoes_a_self_autolink_and_escapes():
    assert spec.plain("[greet.sh](<http://greet.sh>) and tests/\\*\\_test.sh") == "greet.sh and tests/*_test.sh"
    assert spec.plain("[docs](<https://x.io>)") == "[docs](<https://x.io>)"
    assert spec.plain("a \\`b\\` c") == "a `b` c"
    assert spec.plain("`[x.io](<http://x.io>) \\*`") == "`[x.io](<http://x.io>) \\*`"
    assert spec.plain("a ` b \\_c\n\nlater `x` \\*") == "a ` b _c\n\nlater `x` *"
    fenced = "```\n[x.io](<http://x.io>) \\_\n```\n[y.io](<http://y.io>)"
    assert spec.plain(fenced) == "```\n[x.io](<http://x.io>) \\_\n```\ny.io"


def test_gather_returns_plain_fields_and_the_saved_section_stays_raw():
    store = feature_store()
    feature, _ = loaded(store)
    raw = "See [x.io](<http://x.io>), a\\_b."
    planning.save_section(store, feature, "Examples", raw, False)
    item(store, "s1", "Ids", {"answer": "tests/\\*", "item": "Ids", "question": "Q?"})
    found = spec.gather(store, *loaded(store))
    assert found.examples == f"{planning.WRITTEN}\n\nSee x.io, a_b."
    assert found.decisions[0]["answer"] == "tests/*"
    assert planning.section(store, feature, "Examples") == f"{planning.WRITTEN}\n\n{raw}"
