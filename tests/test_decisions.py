from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from deckhand import answers, decisions
from deckhand.questions import as_tool_input
from deckhand.step import Refusal
from deckhand.store.fake import FakeStore


def ask(tmp_path: Path, kind: str = "contract", stories: list[str] | None = None):
    question = decisions.make_question(kind, "Checkout")
    decisions.save_pending(question, "v1", stories or ["s1"], tmp_path)
    return question, decisions.load_pending(question.nonce, tmp_path)


def answered(tmp_path: Path, question, answer: str, session: str = "sess") -> None:
    answers.record(
        {
            "session_id": session,
            "tool_response": {
                "questions": [{"question": question.text, "header": question.header}],
                "answers": {question.text: answer},
            },
        },
        tmp_path,
    )


def store_with(*ids: str) -> FakeStore:
    store = FakeStore()
    for id in ids:
        store.create_issue(id, "ACME", id, "", None, [])
    store.writes.clear()
    return store


def test_decide_without_recorded_answer_refuses_and_writes_nothing(tmp_path: Path):
    _, pending = ask(tmp_path)
    store = store_with("s1")
    with pytest.raises(Refusal, match="no recorded answer"):
        decisions.decide(store, pending, tmp_path)
    assert store.writes == []


def test_decide_with_answer_copied_from_agent_text_refuses(tmp_path: Path):
    question, pending = ask(tmp_path, "decision-item")
    answered(tmp_path, question, "ship it as drafted")
    store = store_with("s1")
    with pytest.raises(Refusal, match="copied from the agent text"):
        decisions.decide(store, pending, tmp_path, "I will say ship it as drafted today")
    assert store.writes == []


def test_decide_with_option_description_refuses(tmp_path: Path):
    question, pending = ask(tmp_path)
    answered(tmp_path, question, "contract: Checkout")
    store = store_with("s1")
    with pytest.raises(Refusal, match="option description"):
        decisions.decide(store, pending, tmp_path)
    assert store.writes == []


def test_decide_records_on_every_story(tmp_path: Path):
    question, pending = ask(tmp_path, stories=["s1", "s2"])
    answered(tmp_path, question, "Approve", "sess-9")
    store = store_with("s1", "s2")
    assert decisions.decide(store, pending, tmp_path, "Approve the contract") is True
    for story in ("s1", "s2"):
        [record] = store.records(story)
        assert (record.kind, record.subject, record.version) == ("contract", "Checkout", "v1")
        assert record.author == "person:sess-9"
        assert record.data == {"answer": "Approve", "question": question.text, "nonce": question.nonce}


def test_decide_twice_writes_once(tmp_path: Path):
    question, pending = ask(tmp_path)
    answered(tmp_path, question, "Approve")
    store = store_with("s1")
    decisions.decide(store, pending, tmp_path)
    decisions.decide(store, pending, tmp_path)
    assert [w[0] for w in store.writes] == ["put_record"]


@pytest.mark.parametrize(
    "kind,answer", [("contract", "Not yet"), ("decision-item", "Change"), ("decision-item", "Discuss")]
)
def test_non_positive_answers_write_no_record(tmp_path: Path, kind: str, answer: str):
    question, pending = ask(tmp_path, kind)
    answered(tmp_path, question, answer)
    store = store_with("s1")
    assert decisions.decide(store, pending, tmp_path) is False
    assert store.writes == []


def test_tier_selection_creates_a_record(tmp_path: Path):
    question, pending = ask(tmp_path, "tier")
    assert question.multi
    assert len(question.options) == 5
    answered(tmp_path, question, question.options[0])
    store = store_with("s1")
    assert decisions.decide(store, pending, tmp_path) is True
    assert store.records("s1")[0].kind == "tier"


def test_question_text_is_ascii_and_names_kind_and_subject():
    question = decisions.make_question("state-chart", "Checkout")
    assert question.text.isascii()
    assert "state-chart" in question.text and "Checkout" in question.text
    assert question.options == ["Approve", "Not yet"]


def test_unknown_kind_refuses():
    with pytest.raises(Refusal, match="unknown decision kind"):
        decisions.make_question("whim", "x")


def test_pending_file_is_private(tmp_path: Path):
    question, _ = ask(tmp_path)
    path = decisions.pending_path(question.nonce, tmp_path)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert json.loads(path.read_text())["question"] == question.text
    assert as_tool_input(question)["questions"][0]["question"] == question.text
