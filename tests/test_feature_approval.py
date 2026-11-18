from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import answers, features, ops, planning, versions
from deckhand.features import Phase
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.steps.feature import Feature
from deckhand.store.fake import FakeStore
from deckhand.tiers import FACTS

TEAM = ["--team-key", "ACME"]
STATE_CHART = next(f.question for f in FACTS if f.key == "multi_step")
CONTRACT = next(f.question for f in FACTS if f.key == "crosses_repositories")


def setup() -> FakeStore:
    store = FakeStore()
    store.create_project("p1", "ACME", "Alpha", "")
    store.set_project_dates("p1", "2026-11-16", "2026-11-23")
    feature = load(store)
    planning.save_section(store, feature, "Contract", "POST /orders returns 201.", False)
    planning.save_section(store, feature, "Invariants", "A total is never negative.", False)
    store.create_issue("s1", "ACME", "Orders", "Outcome: an order is placed.", "p1", [])
    store.create_issue("s2", "ACME", "Receipts", "Outcome: a receipt is sent.", "p1", [])
    return store


def load(store: FakeStore) -> features.Feature:
    found = features.open_feature(store, "ACME")
    assert found is not None
    return found


def decided(store: FakeStore, kind: str, subject: str, answer: str, stories: list[str]) -> None:
    version = versions.version_for(store, load(store), kind, subject)
    data = {"answer": answer}
    if kind == "decision-item":
        subject, data = versions.item_subject(subject), {**data, "item": subject}
    for story in stories:
        ops.ensure_record(store, story, Record(kind, subject, version, "person:s", data))


def sat(store: FakeStore, story: str, tier_answer: str = CONTRACT) -> None:
    decided(store, "tier", story, tier_answer, [story])
    decided(store, "sitting", story, "Done", [story])


def approve_everything(store: FakeStore) -> None:
    decided(store, "contract", "p1", "Approve", ["s1", "s2"])
    decided(store, "invariants", "p1", "Approve", ["s1", "s2"])
    sat(store, "s1")
    sat(store, "s2")
    assert features.approval_gaps(store, load(store)) == []
    decided(store, "feature-approval", "p1", "Approve", ["s1", "s2"])


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
    decided(store, "contract", "p1", "Approve", ["s1", "s2"])
    decided(store, "invariants", "p1", "Approve", ["s1"])
    sat(store, "s1", STATE_CHART)
    decided(store, "decision-item", "Keep orders in one table", "Change", ["s1"])
    assert features.approval_gaps(store, load(store)) == [
        "the Invariants are not approved",
        "ACME-1: the state chart is not approved",
        "ACME-2: the tier is not decided",
        "ACME-2: the sitting is not done",
        "decision item Keep orders in one table is open (Change)",
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
    decided(store, "contract", "p1", "Approve", ["s1"])
    decided(store, "invariants", "p1", "Approve", ["s1"])
    sat(store, "s1")
    assert features.approval_gaps(store, load(store)) == []
    decided(store, "feature-approval", "p1", "Approve", ["s1"])
    assert features.phase(load(store)) is Phase.BUILDING


def test_kept_decision_item_is_closed():
    store = setup()
    decided(store, "decision-item", "Keep orders in one table", "Change", ["s1"])
    decided(store, "decision-item", "Keep orders in one table", "Keep", ["s1"])
    assert not any("decision item" in gap for gap in features.approval_gaps(store, load(store)))


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
    store.create_project("p1", "ACME", "Alpha", "")
    store.set_project_dates("p1", "2026-11-16", "2026-11-23")
    store.create_issue("s1", "ACME", "Orders", "Outcome.", "p1", [])
    with pytest.raises(Refusal, match="the Invariants section is not saved"):
        Decide(store).context(["invariants", "p1", *TEAM])
    assert store.records("s1") == []


def test_decide_context_refuses_feature_approval_with_gaps(state: Path):
    store = setup()
    with pytest.raises(Refusal, match="not ready for approval: the Contract is not approved"):
        Decide(store).context(["feature-approval", "p1", *TEAM])


def test_feature_approval_through_the_dialog_reaches_building(state: Path, capsys: pytest.CaptureFixture[str]):
    store = setup()
    decided(store, "contract", "p1", "Approve", ["s1", "s2"])
    decided(store, "invariants", "p1", "Approve", ["s1", "s2"])
    sat(store, "s1")
    sat(store, "s2")
    step = Decide(store)
    step.context(["feature-approval", "p1", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    asked = shown["tool_input"]["questions"][0]
    answers.record(
        {
            "session_id": "sess",
            "tool_response": {
                "questions": [{"question": asked["question"], "header": asked["header"]}],
                "answers": {asked["question"]: "Approve"},
            },
        },
        state,
    )
    step.apply([shown["nonce"], *TEAM])
    assert features.phase(load(store)) is Phase.BUILDING
