from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import answers, approval, asks, features, ops, planning, queue, review, versions
from deckhand.features import FEATURE_LABEL, Phase
from deckhand.ops import create_id
from deckhand.records import Record, content_hash
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.steps.feature import Feature
from deckhand.steps.stories import map_records
from deckhand.store.fake import FakeStore

P1 = create_id("ACME:Alpha", "feature")

TEAM = ["--team-key", "ACME"]


def setup(store: FakeStore | None = None, unsaved: tuple[str, ...] = ()) -> FakeStore:
    store = store if store is not None else FakeStore()
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    feature = load(store)
    for name in (n for n in planning.SECTIONS if n not in unsaved):
        planning.save_section(store, feature, name, f"The {name} of Alpha.", False)
    store.create_issue("s1", "ACME", "Orders", "Outcome: an order is placed.", P1, ["widgets"])
    store.create_issue("s2", "ACME", "Receipts", "Outcome: a receipt is sent.", P1, ["widgets"])
    return store


def load(store: FakeStore) -> features.Feature:
    found = features.open_feature(store, "ACME")
    assert found is not None
    return found


DRAFT = "Yes"


def decided(store: FakeStore, kind: str, subject: str, answer: str, stories: list[str]) -> None:
    data: dict = {"answer": answer}
    if kind == "decision-item":
        version = versions.item_version(subject, DRAFT)
        subject, data = versions.item_subject(subject), {**data, "item": subject}
    else:
        version = versions.version_for(store, load(store), kind, subject)
    for story in stories:
        ops.ensure_record(store, story, Record(kind, subject, version, "person:s", data))


def mapped(store: FakeStore, story: str, tier: str = "none", chart: str = "") -> None:
    for record in map_records(story, {"tier": tier, "state_chart": chart}):
        ops.ensure_record(store, story, record)


def reviewed(store: FakeStore) -> None:
    target = content_hash(features.review_text(store, load(store)))
    clean = review.round_record(P1, "feature", review.RoundPlan(1, scoped=False, base_hash=""), target, [])
    for story in load(store).live:
        ops.ensure_record(store, story["id"], clean)


def approve_everything(store: FakeStore) -> None:
    reviewed(store)
    for story in ("s1", "s2"):
        if versions.held(store.records(story), "tier", story) is None:
            mapped(store, story)
    assert features.approval_gaps(store, load(store)) == []
    reapprove(store)


def reapprove(store: FakeStore) -> None:
    decided(store, "feature-approval", P1, "Approve", [s["id"] for s in load(store).live])


def test_approval_gaps_lists_each_missing_decision():
    store = setup()
    assert features.approval_gaps(store, load(store)) == [
        "ACME-1: the story map gives no tier; save the map again",
        "ACME-2: the story map gives no tier; save the map again",
        "the feature review has not run",
    ]
    mapped(store, "s1", "multi_step")
    mapped(store, "s2")
    assert features.approval_gaps(store, load(store)) == [
        "ACME-1: tier 2 needs a state chart in the story map",
        "the feature review has not run",
    ]


def test_approval_gaps_name_each_unsaved_section_first():
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    store.create_issue("s1", "ACME", "Orders", "Outcome.", P1, [])
    planning.save_section(store, load(store), "Shape", "The shape.", False)
    planning.save_section(store, load(store), "Contract", "The contract.", False)
    gaps = features.approval_gaps(store, load(store))
    assert gaps[:6] == [
        "the Frame section is not saved",
        "the Examples section is not saved",
        "the Pre-mortem section is not saved",
        "the Facts section is not saved",
        "the Invariants section is not saved",
        "ACME-1: the story map gives no tier; save the map again",
    ]


def test_changed_contract_voids_feature_approval():
    store = setup()
    approve_everything(store)
    assert features.phase(load(store)) is Phase.BUILDING
    planning.save_section(store, load(store), "Contract", "POST /orders returns 202.", False)
    feature = load(store)
    assert feature.reapproval_due
    assert features.approval_gaps(store, feature) == [
        "the feature review has not reviewed the current contract and stories; run a feature round",
    ]


def test_changed_story_voids_feature_approval():
    store = setup()
    approve_everything(store)
    store.set_issue_text("s2", "Receipts by email", "Outcome: a receipt is sent.")
    feature = load(store)
    assert feature.reapproval_due
    assert features.approval_gaps(store, feature) == [
        "the feature review has not reviewed the current contract and stories; run a feature round",
    ]


def test_canceled_story_needs_no_approval():
    store = setup()
    store.set_issue_state("s2", "Canceled")
    mapped(store, "s1")
    reviewed(store)
    assert features.approval_gaps(store, load(store)) == []
    decided(store, "feature-approval", P1, "Approve", ["s1"])
    assert features.phase(load(store)) is Phase.BUILDING


def expect(store: FakeStore, story: str, key: str, *items: str) -> None:
    data = {"unknowns": [], "new_here": [], "decisions": [{"item": i, "draft": DRAFT} for i in items]}
    store.put_record(story, Record("stub", key, "1", "agent:stories", data))


@pytest.mark.parametrize("answer", ["Yes", "Salut"])
def test_expected_decision_item_blocks_approval_until_answered(answer: str):
    store = setup()
    approve_everything(store)
    expect(store, "s2", "receipts", "Send receipts by email")
    assert features.approval_gaps(store, load(store)) == ["ACME-2: decision item Send receipts by email is not decided"]
    decided(store, "decision-item", "Send receipts by email", answer, ["s2"])
    assert features.approval_gaps(store, load(store)) == []


def test_an_answer_in_own_words_stays_decided_once_the_map_takes_it_as_the_draft():
    store = setup()
    approve_everything(store)
    expect(store, "s2", "receipts", "Send receipts by email")
    decided(store, "decision-item", "Send receipts by email", "Only on request", ["s2"])
    rewritten = {"decisions": [{"item": "Send receipts by email", "draft": "Only on request"}]}
    store.put_record("s2", Record("stub", "receipts", "1", "agent:stories", rewritten))
    assert features.undecided(load(store), load(store).live[1]) == []
    assert features.approval_gaps(store, load(store)) == []
    other = {"decisions": [{"item": "Send receipts by email", "draft": "Always"}]}
    store.put_record("s2", Record("stub", "receipts", "1", "agent:stories", other))
    assert features.undecided(load(store), load(store).live[1]) == ["Send receipts by email"]


def test_expected_decision_item_answered_on_another_story_still_blocks():
    store = setup()
    expect(store, "s2", "receipts", "Send receipts by email")
    decided(store, "decision-item", "Send receipts by email", "Keep", ["s1"])
    assert "ACME-2: decision item Send receipts by email is not decided" in features.approval_gaps(store, load(store))


@pytest.mark.parametrize("answer", ["Yes", "Salut"])
def test_any_answer_settles_a_decision_item(answer: str):
    store = setup()
    decided(store, "decision-item", "Keep orders in one table", answer, ["s1"])
    assert not any("decision item" in gap for gap in features.approval_gaps(store, load(store)))


@pytest.mark.parametrize("canceled", ["s1", "s2"])
def test_decision_item_only_on_a_dropped_story_is_open_whatever_the_order(canceled: str):
    store = setup()
    decided(store, "decision-item", "Keep orders in one table", "Change", ["s1", "s2"])
    decided(store, "decision-item", "Keep orders in one table", "Keep", [canceled])
    store.set_issue_state(canceled, "Canceled")
    gaps = features.approval_gaps(store, load(store))
    assert not any("decision item" in gap for gap in gaps)
    store.set_issue_state("s1" if canceled == "s2" else "s2", "Duplicate")
    store.create_issue("s3", "ACME", "Refunds", "Outcome.", P1, [])
    gaps = features.approval_gaps(store, load(store))
    assert "decision item Keep orders in one table is open" in gaps


def test_a_tier_record_without_a_level_asks_for_the_map_again():
    store = setup()
    store.put_record("s1", Record("tier", "s1", "v1", "person:s", {"answer": "x", "facts": ["multi_step"]}))
    gaps = features.approval_gaps(store, load(store))
    assert "ACME-1: the story map gives no tier; save the map again" in gaps


def test_a_missing_chart_gap_shows_in_feature_context(capsys: pytest.CaptureFixture[str]):
    store = setup()
    mapped(store, "s1", "multi_step")
    Feature(store).context([*TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)["open"][0]["gaps"]
    assert "ACME-1: tier 2 needs a state chart in the story map" in shown


def test_feature_context_names_the_gaps(capsys: pytest.CaptureFixture[str]):
    store = setup()
    Feature(store).context([*TEAM, "--json"])
    held = json.loads(capsys.readouterr().out)["open"][0]
    assert held["phase"] == "planning"
    assert held["gaps"][0] == "ACME-1: the story map gives no tier; save the map again"
    Feature(store).context(TEAM)
    assert "gap: ACME-1: the story map gives no tier; save the map again" in capsys.readouterr().out


def test_an_invariants_edit_voids_the_approval_with_no_gap(capsys: pytest.CaptureFixture[str]):
    store = setup()
    approve_everything(store)
    planning.save_section(store, load(store), "Invariants", "A total is zero or more.", False)
    Feature(store).context([*TEAM, "--json"])
    held = json.loads(capsys.readouterr().out)["open"][0]
    assert (held["phase"], held["gaps"], held["approval_void"]) == ("building", [], True)


def test_feature_context_tells_the_captain_how_to_propose_a_delivery_finding(capsys: pytest.CaptureFixture[str]):
    store = setup()
    target = content_hash(features.review_text(store, load(store)))
    item = {"lens": "scope", "severity": "P2", "path": "Contract", "line": 1, "summary": "Ships late."}
    item |= {"fix": "Say when.", "evidence": "Contract", "confidence": 0.9, "changes_delivery": True}
    found = review.parse_findings(json.dumps([item]))
    round = review.round_record(P1, "feature", review.RoundPlan(1, scoped=False, base_hash=""), target, found)
    for story in load(store).live:
        ops.ensure_record(store, story["id"], round)
    [finding] = round.data["findings"]
    Feature(store).context([*TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)["open"][0]["gaps"]
    assert any(gap.startswith(f"{finding['id']} changes what the feature delivers; propose") for gap in shown)
    person = f"finding {finding['id']} changes what the feature delivers; it waits for a proposal"
    assert person in features.approval_gaps(store, load(store))


def test_feature_context_says_a_frame_change_voided_the_approval(capsys: pytest.CaptureFixture[str]):
    store = setup()
    approve_everything(store)
    planning.save_section(store, load(store), "Frame", "Why orders matter.", False)
    Feature(store).context([*TEAM, "--json"])
    held = json.loads(capsys.readouterr().out)["open"][0]
    assert (held["phase"], held["gaps"], held["approval_void"]) == ("building", [], True)
    Feature(store).context(TEAM)
    assert "approval: the feature changed after it was approved" in capsys.readouterr().out


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def test_decide_context_refuses_feature_approval_with_gaps(state: Path):
    store = setup(unsaved=("Invariants",))
    with pytest.raises(Refusal, match="not ready for approval: the Invariants section is not saved"):
        Decide(store).context(["feature-approval", P1, *TEAM])
    assert store.records("s1") == []


def test_feature_approval_through_the_dialog_reaches_building(state: Path, capsys: pytest.CaptureFixture[str]):
    store = setup()
    mapped(store, "s1")
    mapped(store, "s2")
    reviewed(store)
    step = Decide(store)
    step.context(["feature-approval", P1, *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    asked = shown["tool_input"]["questions"][0]
    answers.record(
        {
            "session_id": "sess",
            "tool_response": {
                "questions": [asked],
                "answers": {asked["question"]: "Approve"},
            },
        },
        state,
    )
    step.apply([shown["nonce"], *TEAM])
    assert features.phase(load(store)) is Phase.BUILDING


def test_a_changed_draft_is_asked_again():
    store = setup()
    expect(store, "s2", "receipts", "Send receipts by email")
    decided(store, "decision-item", "Send receipts by email", "Yes", ["s2"])
    assert not any("is not decided" in gap for gap in features.approval_gaps(store, load(store)))
    data = {"unknowns": [], "new_here": [], "decisions": [{"item": "Send receipts by email", "draft": "By post"}]}
    store.put_record("s2", Record("stub", "receipts", "1", "agent:stories", data))
    assert "ACME-2: decision item Send receipts by email is not decided" in features.approval_gaps(store, load(store))


def test_a_decision_item_added_after_approval_voids_it():
    store = setup()
    expect(store, "s2", "receipts", "Send receipts by email")
    decided(store, "decision-item", "Send receipts by email", "Keep", ["s2"])
    approve_everything(store)
    assert features.phase(load(store)) is Phase.BUILDING
    expect(store, "s2", "receipts", "Send receipts by email", "Keep receipts a year")
    feature = load(store)
    assert feature.reapproval_due
    assert "ACME-2: decision item Keep receipts a year is not decided" in features.approval_gaps(store, feature)


def test_a_state_chart_edited_after_approval_voids_it():
    store = setup()
    mapped(store, "s1", "multi_step", "placed -> paid")
    approve_everything(store)
    mapped(store, "s1", "multi_step", "placed -> paid")
    assert features.phase(load(store)) is Phase.BUILDING
    mapped(store, "s1", "multi_step", "placed -> paid -> shipped")
    assert load(store).reapproval_due


def test_a_tier_two_story_without_a_chart_is_a_gap():
    store = setup()
    [tier, _] = map_records("s1", {"tier": "multi_step", "state_chart": "a -> b"})
    store.put_record("s1", tier)
    store.put_record("s1", Record("state-chart", "s1", "v1", "person:s", {"answer": "Approve"}))
    assert "ACME-1: tier 2 needs a state chart in the story map" in features.approval_gaps(store, load(store))


def test_a_tier_written_by_a_person_is_left_unread():
    store = setup()
    data = {"answer": "None of these", "level": 0, "fact": "none"}
    store.put_record("s1", Record("tier", "s1", "v1", "person:s", data))
    assert "ACME-1: the story map gives no tier; save the map again" in features.approval_gaps(store, load(store))


def test_a_new_dependency_voids_the_approval():
    store = setup()
    approve_everything(store)
    assert features.phase(load(store)) is Phase.BUILDING
    store.relate("s1", "s2")
    assert load(store).reapproval_due


def test_a_new_repository_leaves_the_approval_standing():
    store = setup()
    store.add_repository_label("ACME", "widgets")
    store.add_repository_label("ACME", "gadgets")
    approve_everything(store)
    store.set_labels("s1", ["gadgets"])
    assert features.phase(load(store)) is Phase.BUILDING
    assert "repository: gadgets" in approval.screen(store, load(store))


def test_an_untaken_draft_holds_the_approval():
    store = setup()
    reviewed(store)
    mapped(store, "s1")
    mapped(store, "s2")
    store.put_record("s1", Record(queue.DRAFT, "s1", "d1", "agent:draft", {}))
    assert features.approval_gaps(store, load(store)) == ["ACME-1: its draft is not taken"]
    store.put_record("s1", Record(queue.TAKEN, "s1", "d1", "agent:captain", {}))
    assert features.approval_gaps(store, load(store)) == []


def test_a_draft_being_written_holds_the_approval():
    store = setup()
    reviewed(store)
    mapped(store, "s1")
    mapped(store, "s2")
    story = features.story(store, load(store), "ACME-2")
    store.put_record("s2", asks.hand_start(story, store.records("s2"), queue.DRAFT_HAND))
    assert features.approval_gaps(store, load(store)) == ["ACME-2: its draft is being written"]
