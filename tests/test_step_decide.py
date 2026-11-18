from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import answers, decisions, features, planning, versions
from deckhand.features import FEATURE_LABEL
from deckhand.ops import create_id
from deckhand.records import content_hash
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.store.fake import FakeStore

P1 = create_id("ACME:Alpha", "feature")

TEAM = ["--team-key", "ACME"]


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def feature_store() -> FakeStore:
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    store.create_issue("s1", "ACME", "One", "Outcome.", P1, [])
    store.create_issue("s2", "ACME", "Two", "Outcome.", P1, [])
    store.create_issue("x1", "ACME", "Elsewhere", "", None, [])
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    planning.save_section(store, feature, "Contract", "POST /orders.", False)
    return store


def record_answer(state: Path, asked: dict, answer: str) -> None:
    answers.record(
        {
            "session_id": "sess",
            "tool_response": {
                "questions": [asked],
                "answers": {asked["question"]: answer},
            },
        },
        state,
    )


def test_pending_question_round_trips_through_context_and_apply(state: Path, capsys):
    store = feature_store()
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    version = versions.version_for(store, feature, "contract", P1)
    step = Decide(store)
    step.context(["contract", P1, *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    nonce = shown["nonce"]
    asked = shown["tool_input"]["questions"][0]
    assert asked["header"] == f"dh {nonce}"
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
    step.apply([nonce, *TEAM])
    for story in ("s1", "s2"):
        [record] = store.records(story)
        assert (record.kind, record.version, record.author) == ("contract", version, "person:sess")
    assert store.records("x1") == []
    assert not decisions.pending_path(nonce, state).exists()


def test_story_decision_pins_the_story_by_reference(state: Path, capsys):
    store = feature_store()
    Decide(store).context(["sitting", "ACME-2", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    record_answer(state, shown["tool_input"]["questions"][0], "Done")
    Decide(store).apply([shown["nonce"], *TEAM])
    [record] = store.records("s2")
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    assert (record.kind, record.subject) == ("sitting", "s2")
    assert record.version == versions.version_for(store, feature, "sitting", "s2")
    assert store.records("s1") == []


def test_decision_item_shows_its_text_and_records_an_open_answer(state: Path, capsys):
    store = feature_store()
    text = "Keep orders in one table"
    Decide(store).context(["decision-item", text, "--story", "ACME-1", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    asked = shown["tool_input"]["questions"][0]
    assert text in asked["question"]
    record_answer(state, asked, "Change")
    Decide(store).apply([shown["nonce"], *TEAM])
    [record] = store.records("s1")
    assert (record.subject, record.version) == (versions.item_subject(text), content_hash(text))
    assert (record.data["answer"], record.data["item"]) == ("Change", text)


@pytest.mark.parametrize(
    "args,match",
    [
        (["contract", "p9"], "p9 is not the open feature"),
        (["contract", P1, "--story", "s1"], "--story applies only to decision-item"),
        (["tier", "ACME-3"], "ACME-3 is not a story of Alpha"),
        (["decision-item", "Keep it"], "decision-item needs at least one --story"),
        (["contract", P1, "--version", "v1"], "unknown argument --version"),
    ],
)
def test_context_refuses_what_it_cannot_pin(state: Path, args: list[str], match: str):
    with pytest.raises(Refusal, match=match):
        Decide(feature_store()).context([*args, *TEAM])


def test_apply_without_pending_refuses(state: Path):
    with pytest.raises(Refusal, match="no pending question"):
        Decide(FakeStore()).apply(["deadbeef", *TEAM])


def test_questions_show_identifiers_titles_and_the_feature_name(state: Path, capsys):
    store = feature_store()
    Decide(store).context(["contract", P1, *TEAM, "--json"])
    text = json.loads(capsys.readouterr().out)["tool_input"]["questions"][0]["question"]
    assert "Alpha" in text and "ACME-1 One, ACME-2 Two" in text
    assert P1 not in text and "s1" not in text
    Decide(store).context(["sitting", "ACME-2", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)["tool_input"]["questions"][0]
    assert "ACME-2 Two" in shown["question"] and "s2" not in shown["question"]
    assert all("s2" not in option["description"] for option in shown["options"])
