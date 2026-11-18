from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from deckhand import answers, decisions, tiers
from deckhand.questions import as_tool_input
from deckhand.records import content_hash
from deckhand.step import Refusal
from deckhand.store.fake import FakeStore


def pin(stories: list[str] | None = None, version: str = "v1") -> decisions.Pin:
    stories = stories or ["s1"]
    return decisions.Pin("Checkout", version, stories, label="Checkout", story_labels=[f"ACME-{s}" for s in stories])


def ask(tmp_path: Path, kind: str = "contract", stories: list[str] | None = None):
    question = decisions.make_question(kind, pin(stories))
    decisions.save_pending(question, pin(stories), tmp_path)
    return question, decisions.load_pending(question.nonce, tmp_path)


def answered(tmp_path: Path, question, answer: str, session: str = "sess", **changes) -> None:
    shown = {**as_tool_input(question)["questions"][0], **changes}
    answers.record(
        {"session_id": session, "tool_response": {"questions": [shown], "answers": {question.text: answer}}},
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


def test_decide_without_an_answers_file_names_the_file(tmp_path: Path):
    _, pending = ask(tmp_path)
    store = store_with("s1")
    with pytest.raises(Refusal, match=str(tmp_path / answers.FILE)):
        decisions.decide(store, pending, tmp_path)
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
    assert decisions.decide(store, pending, tmp_path) is True
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


@pytest.mark.parametrize("kind,answer", [("contract", "Not yet"), ("sitting", "Not yet")])
def test_non_positive_answers_write_no_record(tmp_path: Path, kind: str, answer: str):
    question, pending = ask(tmp_path, kind)
    answered(tmp_path, question, answer)
    store = store_with("s1")
    assert decisions.decide(store, pending, tmp_path) is False
    assert store.writes == []


def test_question_text_is_ascii_and_names_kind_and_subject():
    question = decisions.make_question("state-chart", pin())
    assert question.text.isascii()
    assert "state-chart" in question.text and "Checkout" in question.text
    assert question.options == ["Approve", "Not yet"]


def test_unknown_kind_refuses():
    with pytest.raises(Refusal, match="unknown decision kind"):
        decisions.make_question("whim", pin())


def test_pending_file_is_private(tmp_path: Path):
    question, _ = ask(tmp_path)
    path = decisions.pending_path(question.nonce, tmp_path)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert json.loads(path.read_text())["question"] == question.text
    assert as_tool_input(question)["questions"][0]["question"] == question.text


def edit_pending(tmp_path: Path, nonce: str, **changes) -> decisions.Pending:
    path = decisions.pending_path(nonce, tmp_path)
    data = json.loads(path.read_text())
    data.update(changes)
    path.write_text(json.dumps(data))
    return decisions.load_pending(nonce, tmp_path)


@pytest.mark.parametrize(
    "changes",
    [
        {"stories": ["s1", "s2"]},
        {"version": "v2"},
        {"subject": "Other"},
        {"detail": "Other item"},
        {"label": "Other"},
        {"story_labels": ["ACME-9 Other"]},
    ],
)
def test_pending_edited_after_the_answer_is_refused(tmp_path: Path, changes: dict):
    question, _ = ask(tmp_path)
    answered(tmp_path, question, "Approve")
    pending = edit_pending(tmp_path, question.nonce, **changes)
    store = store_with("s1", "s2")
    with pytest.raises(Refusal, match="differs from the question the person saw"):
        decisions.decide(store, pending, tmp_path)
    assert store.writes == []


def test_question_text_states_version_and_story_labels_not_ids():
    shown = decisions.Pin("p1", "v1", ["u1", "u2"], label="Alpha", story_labels=["ACME-1 Orders", "ACME-2 Receipts"])
    question = decisions.make_question("contract", shown)
    assert "Alpha" in question.text
    assert "ACME-1 Orders, ACME-2 Receipts" in question.text
    assert content_hash("v1")[:12] in question.text
    assert not any(id in question.text for id in ("p1", "u1", "u2"))
    assert question.text.isascii()
    assert (
        decisions.make_question(
            "contract", decisions.Pin("p1", "v1", ["u1", "u3"], label="Alpha", story_labels=shown.story_labels)
        ).text
        != question.text
    )


def test_tier_is_one_choice_of_four_ending_with_none_of_these():
    question = decisions.make_question("tier", pin())
    assert question.options == tiers.OPTIONS
    assert len(question.options) == 4 and question.options[-1] == "None of these"
    assert as_tool_input(question)["questions"][0]["multiSelect"] is False
    assert "highest" in question.text


@pytest.mark.parametrize("index,level,fact", [(0, 3, "concurrency"), (2, 1, "data_rule"), (3, 0, "none")])
def test_tier_record_holds_the_chosen_level_and_fact(tmp_path: Path, index: int, level: int, fact: str):
    question, pending = ask(tmp_path, "tier")
    answered(tmp_path, question, question.options[index])
    store = store_with("s1")
    assert decisions.decide(store, pending, tmp_path) is True
    [record] = store.records("s1")
    assert (record.data["level"], record.data["fact"]) == (level, fact)
    assert "facts" not in record.data


@pytest.mark.parametrize("answer", ["anything at all", "None of these, Jobs, retries, webhooks, queues or two writers"])
def test_tier_answer_other_than_one_option_refuses(tmp_path: Path, answer: str):
    question, pending = ask(tmp_path, "tier")
    answered(tmp_path, question, answer)
    store = store_with("s1")
    with pytest.raises(Refusal, match="not one of the offered options"):
        decisions.decide(store, pending, tmp_path)
    assert store.writes == []


def test_answer_marked_copied_refuses(tmp_path: Path):
    question, pending = ask(tmp_path, "decision-item")
    row = {
        "session_id": "s",
        "header": question.header,
        "question": question.text,
        "answer": "x" * 25,
        "copied": True,
        "options": as_tool_input(question)["questions"][0]["options"],
        "multiSelect": False,
    }
    (tmp_path / answers.FILE).write_text(json.dumps(row) + "\n")
    (tmp_path / answers.FILE).chmod(0o600)
    store = store_with("s1")
    with pytest.raises(Refusal, match="copied from recent agent output"):
        decisions.decide(store, pending, tmp_path)
    assert store.writes == []


@pytest.mark.parametrize("answer", ["Keep", "Change", "Discuss"])
def test_every_decision_item_answer_is_recorded(tmp_path: Path, answer: str):
    question, pending = ask(tmp_path, "decision-item")
    answered(tmp_path, question, answer)
    store = store_with("s1")
    assert decisions.decide(store, pending, tmp_path) is True
    [record] = store.records("s1")
    assert record.data["answer"] == answer


def test_load_pending_refuses_a_nonce_outside_the_pending_folder(tmp_path: Path):
    question, _ = ask(tmp_path)
    escaped = tmp_path / "x.json"
    escaped.write_text(decisions.pending_path(question.nonce, tmp_path).read_text())
    with pytest.raises(Refusal, match="not a deckhand nonce"):
        decisions.load_pending("../x", tmp_path)


@pytest.mark.parametrize(
    "changes",
    [
        {"options": [{"label": "Approve", "description": "contract: Checkout"}]},
        {
            "options": [
                {"label": "Not yet", "description": "contract: Checkout"},
                {"label": "Approve", "description": "contract: Checkout"},
            ]
        },
        {
            "options": [
                {"label": "Approve", "description": "Safe"},
                {"label": "Not yet", "description": "contract: Checkout"},
            ]
        },
        {"multiSelect": True},
        {"options": None, "multiSelect": None},
    ],
)
def test_options_the_person_saw_must_match_the_pending_question(tmp_path: Path, changes: dict):
    question, pending = ask(tmp_path)
    answered(tmp_path, question, "Approve", **changes)
    store = store_with("s1")
    with pytest.raises(Refusal, match="options the person saw"):
        decisions.decide(store, pending, tmp_path)
    assert store.writes == []
