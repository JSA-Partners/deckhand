from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import answers, features, ops, planning, tiers, versions
from deckhand.features import FEATURE_LABEL, Phase
from deckhand.ops import create_id
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.steps.feature import Feature
from deckhand.store.fake import FakeStore

P1 = create_id("ACME:Alpha", "feature")

TEAM = ["--team-key", "ACME"]
STATE_CHART = "A flow or an entity with several states"


def setup() -> FakeStore:
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    feature = load(store)
    for name in planning.SECTIONS:
        planning.save_section(store, feature, name, f"The {name} of Alpha.", False)
    store.create_issue("s1", "ACME", "Orders", "Outcome: an order is placed.", P1, [])
    store.create_issue("s2", "ACME", "Receipts", "Outcome: a receipt is sent.", P1, [])
    return store


def load(store: FakeStore) -> features.Feature:
    found = features.open_feature(store, "ACME")
    assert found is not None
    return found


def decided(store: FakeStore, kind: str, subject: str, answer: str, stories: list[str]) -> None:
    version = versions.version_for(store, load(store), kind, subject)
    data: dict = {"answer": answer}
    if kind == "tier":
        tier = tiers.chosen(answer)
        data |= {"level": tier.level, "fact": tier.fact}
    if kind == "decision-item":
        subject, data = versions.item_subject(subject), {**data, "item": subject}
    for story in stories:
        ops.ensure_record(store, story, Record(kind, subject, version, "person:s", data))


def sat(store: FakeStore, story: str, tier_answer: str = tiers.NONE) -> None:
    decided(store, "tier", story, tier_answer, [story])
    decided(store, "sitting", story, "Done", [story])


def approve_everything(store: FakeStore) -> None:
    decided(store, "contract", P1, "Approve", ["s1", "s2"])
    decided(store, "invariants", P1, "Approve", ["s1", "s2"])
    sat(store, "s1")
    sat(store, "s2")
    assert features.approval_gaps(store, load(store)) == []
    decided(store, "feature-approval", P1, "Approve", ["s1", "s2"])


def test_approval_gaps_lists_each_missing_decision():
    store = setup()
    assert features.approval_gaps(store, load(store)) == [
        "the Contract is not approved",
        "the Invariants are not approved",
        "ACME-1: the tier is not decided",
        "ACME-1: the sitting is not done",
        "ACME-2: the tier is not decided",
        "ACME-2: the sitting is not done",
    ]
    decided(store, "contract", P1, "Approve", ["s1", "s2"])
    decided(store, "invariants", P1, "Approve", ["s1"])
    sat(store, "s1", STATE_CHART)
    assert features.approval_gaps(store, load(store)) == [
        "the Invariants are not approved",
        "ACME-1: the state chart is not approved",
        "ACME-2: the tier is not decided",
        "ACME-2: the sitting is not done",
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
        "the Contract is not approved",
    ]


def test_changed_contract_voids_feature_approval():
    store = setup()
    approve_everything(store)
    assert features.phase(load(store)) is Phase.BUILDING
    planning.save_section(store, load(store), "Contract", "POST /orders returns 202.", False)
    feature = load(store)
    assert features.phase(feature) is Phase.PLANNING
    assert features.approval_gaps(store, feature) == ["the Contract changed after it was approved"]


def test_changed_story_voids_feature_approval():
    store = setup()
    approve_everything(store)
    store.set_issue_text("s2", "Receipts by email", "Outcome: a receipt is sent.")
    feature = load(store)
    assert features.phase(feature) is Phase.PLANNING
    assert features.approval_gaps(store, feature) == [
        "ACME-2: the tier changed after it was decided",
        "ACME-2: the sitting changed after it was done",
    ]


def test_canceled_story_needs_no_approval():
    store = setup()
    store.set_issue_state("s2", "Canceled")
    decided(store, "contract", P1, "Approve", ["s1"])
    decided(store, "invariants", P1, "Approve", ["s1"])
    sat(store, "s1")
    assert features.approval_gaps(store, load(store)) == []
    decided(store, "feature-approval", P1, "Approve", ["s1"])
    assert features.phase(load(store)) is Phase.BUILDING


def expect(store: FakeStore, story: str, key: str, *items: str) -> None:
    data = {"unknowns": [], "new_here": [], "decisions": list(items)}
    store.put_record(story, Record("stub", key, "1", "agent:stories", data))


@pytest.mark.parametrize("answer", ["Keep", "Change", "Discuss"])
def test_expected_decision_item_blocks_approval_until_answered(answer: str):
    store = setup()
    approve_everything(store)
    expect(store, "s2", "receipts", "Send receipts by email")
    assert features.approval_gaps(store, load(store)) == ["ACME-2: decision item Send receipts by email is not decided"]
    decided(store, "decision-item", "Send receipts by email", answer, ["s2"])
    assert features.approval_gaps(store, load(store)) == []


def test_expected_decision_item_answered_on_another_story_still_blocks():
    store = setup()
    expect(store, "s2", "receipts", "Send receipts by email")
    decided(store, "decision-item", "Send receipts by email", "Keep", ["s1"])
    assert "ACME-2: decision item Send receipts by email is not decided" in features.approval_gaps(store, load(store))


@pytest.mark.parametrize("answer", ["Keep", "Change", "Discuss"])
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


def test_old_tier_record_of_fact_keys_asks_for_the_tier_again():
    store = setup()
    version = versions.version_for(store, load(store), "tier", "s1")
    old = Record("tier", "s1", version, "person:s", {"answer": "x", "facts": ["multi_step"]})
    store.put_record("s1", old)
    gaps = features.approval_gaps(store, load(store))
    assert "ACME-1: the tier record holds no level; decide the tier again" in gaps


def test_odd_tier_answer_with_a_valid_level_does_not_break_context(capsys: pytest.CaptureFixture[str]):
    store = setup()
    version = versions.version_for(store, load(store), "tier", "s1")
    odd = Record("tier", "s1", version, "person:s", {"answer": "something else", "level": 2, "fact": "multi_step"})
    store.put_record("s1", odd)
    gaps = features.approval_gaps(store, load(store))
    assert "ACME-1: the state chart is not approved" in gaps
    Feature(store).context([*TEAM, "--json"])
    assert "ACME-1: the state chart is not approved" in json.loads(capsys.readouterr().out)["open"][0]["gaps"]


def test_feature_context_names_the_gaps(capsys: pytest.CaptureFixture[str]):
    store = setup()
    approve_everything(store)
    planning.save_section(store, load(store), "Invariants", "A total is zero or more.", False)
    Feature(store).context([*TEAM, "--json"])
    held = json.loads(capsys.readouterr().out)["open"][0]
    assert held["phase"] == "planning"
    assert held["gaps"] == ["the Invariants changed after they were approved"]
    Feature(store).context(TEAM)
    assert "gap: the Invariants changed after they were approved" in capsys.readouterr().out


def test_feature_context_says_a_frame_change_voided_the_approval(capsys: pytest.CaptureFixture[str]):
    store = setup()
    approve_everything(store)
    planning.save_section(store, load(store), "Frame", "Why orders matter.", False)
    Feature(store).context([*TEAM, "--json"])
    held = json.loads(capsys.readouterr().out)["open"][0]
    assert (held["phase"], held["gaps"], held["approval_void"]) == ("planning", [], True)
    Feature(store).context(TEAM)
    assert "approval: the feature changed after it was approved" in capsys.readouterr().out


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def test_decide_context_refuses_approving_a_missing_section(state: Path):
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    store.create_issue("s1", "ACME", "Orders", "Outcome.", P1, [])
    with pytest.raises(Refusal, match="the Invariants section is not saved"):
        Decide(store).context(["invariants", P1, *TEAM])
    assert store.records("s1") == []


def test_decide_context_refuses_feature_approval_with_gaps(state: Path):
    store = setup()
    with pytest.raises(Refusal, match="not ready for approval: the Contract is not approved"):
        Decide(store).context(["feature-approval", P1, *TEAM])


def test_feature_approval_through_the_dialog_reaches_building(state: Path, capsys: pytest.CaptureFixture[str]):
    store = setup()
    decided(store, "contract", P1, "Approve", ["s1", "s2"])
    decided(store, "invariants", P1, "Approve", ["s1", "s2"])
    sat(store, "s1")
    sat(store, "s2")
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


def test_a_decision_item_added_after_approval_voids_it():
    store = setup()
    expect(store, "s2", "receipts", "Send receipts by email")
    decided(store, "decision-item", "Send receipts by email", "Keep", ["s2"])
    approve_everything(store)
    assert features.phase(load(store)) is Phase.BUILDING
    expect(store, "s2", "receipts", "Send receipts by email", "Keep receipts a year")
    feature = load(store)
    assert features.phase(feature) is Phase.PLANNING
    assert "ACME-2: decision item Keep receipts a year is not decided" in features.approval_gaps(store, feature)


def test_a_state_chart_decided_after_approval_voids_it():
    store = setup()
    approve_everything(store)
    decided(store, "state-chart", "s1", "Approve", ["s1"])
    assert features.phase(load(store)) is Phase.PLANNING
