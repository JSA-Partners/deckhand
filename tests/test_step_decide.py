from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import answers, decisions
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.store.fake import FakeStore

TEAM = ["--team-key", "ACME"]


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def test_pending_question_round_trips_through_context_and_apply(state: Path, capsys):
    store = FakeStore()
    store.create_issue("s1", "ACME", "One", "", None, [])
    step = Decide(store)
    step.context(["contract", "Checkout", "--version", "v2", "--story", "s1", "--json"])
    shown = json.loads(capsys.readouterr().out)
    nonce = shown["nonce"]
    asked = shown["tool_input"]["questions"][0]
    assert asked["header"] == f"dh {nonce}"
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
    step.apply([nonce, *TEAM])
    [record] = store.records("s1")
    assert (record.kind, record.version, record.author) == ("contract", "v2", "person:sess")
    assert not decisions.pending_path(nonce, state).exists()


def test_apply_without_pending_refuses(state: Path):
    with pytest.raises(Refusal, match="no pending question"):
        Decide(FakeStore()).apply(["deadbeef", *TEAM])


def test_apply_reads_agent_text_file(state: Path, tmp_path: Path, capsys):
    store = FakeStore()
    store.create_issue("s1", "ACME", "One", "", None, [])
    step = Decide(store)
    step.context(["decision-item", "Plan", "--version", "v1", "--story", "s1", "--json"])
    shown = json.loads(capsys.readouterr().out)
    asked = shown["tool_input"]["questions"][0]
    answers.record(
        {
            "session_id": "sess",
            "tool_response": {
                "questions": [{"question": asked["question"], "header": asked["header"]}],
                "answers": {asked["question"]: "my own words"},
            },
        },
        state,
    )
    text = tmp_path / "agent.txt"
    text.write_text("they said my own words")
    store.writes.clear()
    with pytest.raises(Refusal, match="copied"):
        step.apply([shown["nonce"], *TEAM, "--agent-text-file", str(text)])
    assert store.writes == []
