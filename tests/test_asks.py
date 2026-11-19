from __future__ import annotations

import json

import pytest

from deckhand import asks
from deckhand.records import Record, content_hash
from deckhand.step import Refusal


def question(**over: object) -> str:
    return json.dumps({"question": "Which queue?", "options": ["Redis", "SQS"], "why": "Both fit."} | over)


def stamped(kind: str, at: str, author: str, subject: str = "x") -> Record:
    return Record(kind, subject, "v1", author, {}, at=at)


def test_question_id_is_the_first_twelve_hex_of_the_question_hash():
    found = asks.parse_question(question())
    assert asks.question_id(found["question"]) == asks.question_id("Which queue?")
    assert len(asks.question_id("Which queue?")) == 12
    assert asks.question_id("Which queue?") == content_hash("Which queue?")[:12]
    assert asks.question_id("Which queue?") != asks.question_id("Which store?")


@pytest.mark.parametrize(
    "over,match",
    [
        ({"options": ["Redis", "SQS (Recommended)"]}, "recommended"),
        ({"question": "Is Redis the recommended queue?"}, "recommended"),
        ({"why": "SQS is recommended by ops"}, "recommended"),
        ({"options": ["a", "b", "c", "d", "e"]}, "2 to 4 options"),
        ({"options": ["a"]}, "2 to 4 options"),
        ({"options": ["a", " "]}, "empty option"),
        ({"options": ["a", "A "]}, "duplicate option"),
        ({"question": " "}, "question must be"),
        ({"why": 3}, "why must be"),
    ],
)
def test_parse_question_refuses(over: dict, match: str):
    with pytest.raises(Refusal, match=match):
        asks.parse_question(question(**over))


def test_parse_question_refuses_what_is_not_the_shape():
    for text in ("not json", "[]", json.dumps({"question": "q", "options": ["a", "b"], "why": "w", "extra": 1})):
        with pytest.raises(Refusal):
            asks.parse_question(text)


def test_wake_due_after_decision_until_hand_writes():
    start = stamped("hand-start", "2026-11-19T10:00:00.000Z", "agent:hand")
    ask = stamped("hand-question", "2026-11-19T10:05:00.000Z", "agent:hand")
    assert asks.wake_due([start, ask]) is None
    answer = stamped("question", "2026-11-19T10:10:00.000Z", "person:s1", "abc")
    assert asks.wake_due([start, ask, answer]) == answer
    later = stamped("finding", "2026-11-19T10:20:00.000Z", "person:s1", "f1")
    assert asks.wake_due([start, ask, answer, later]) == later
    woke = stamped("wake", "2026-11-19T10:30:00.000Z", "agent:captain")
    assert asks.wake_due([start, ask, answer, later, woke]) is None
    wrote = stamped("review-round", "2026-11-19T10:40:00.000Z", "agent:review")
    newer = stamped("sign-off", "2026-11-19T10:50:00.000Z", "person:s1")
    assert asks.wake_due([start, ask, answer, later, woke, wrote, newer]) == newer


def test_wake_due_ignores_stories_without_a_hand_and_decisions_a_hand_does_not_wait_on():
    tier = stamped("tier", "2026-11-19T10:10:00.000Z", "person:s1")
    assert asks.wake_due([tier]) is None
    assert asks.wake_due([stamped("question", "2026-11-19T10:10:00.000Z", "person:s1")]) is None
    start = stamped("hand-start", "2026-11-19T10:00:00.000Z", "agent:hand")
    assert asks.wake_due([start, tier, stamped("sitting", "2026-11-19T10:11:00.000Z", "person:s1")]) is None
    edit = stamped("config-edit-request", "2026-11-19T10:12:00.000Z", "agent:hand")
    assert asks.wake_due([start, tier, edit]) is None


def test_open_proposals_are_those_without_a_person_decision():
    proposals = [asks.proposal_record("f1", "v1", "noise"), asks.proposal_record("f2", "v1", "noise")]
    decision = Record("finding", "f1", "v1", "person:s1", {"answer": "Accept"})
    assert [r.subject for r in asks.open_proposals([*proposals, decision])] == ["f2"]
