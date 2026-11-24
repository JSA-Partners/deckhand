from __future__ import annotations

import json

import pytest

from deckhand import asks, review
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
    ask = stamped("hand-question", "2026-11-19T10:05:00.000Z", "agent:hand", "s1:abc")
    assert asks.wake_due([start, ask], "s1") is None
    answer = stamped("question", "2026-11-19T10:10:00.000Z", "person:s1", "abc")
    assert asks.wake_due([start, ask, answer], "s1") == answer
    round = Record("review-round", "s1:branch:1", "r1", "agent:review", {"findings": [{"id": "f1"}]})
    proposed = asks.proposal_record("f1", "r1", "noise")
    later = stamped("finding", "2026-11-19T10:20:00.000Z", "person:s1", "f1")
    held = [start, round, proposed, ask, answer, later]
    assert asks.wake_due(held, "s1") == later
    woke = stamped("wake", "2026-11-19T10:30:00.000Z", "agent:captain")
    assert asks.wake_due([*held, woke], "s1") is None
    wrote = stamped("review-round", "2026-11-19T10:40:00.000Z", "agent:review")
    newer = stamped("sign-off", "2026-11-19T10:50:00.000Z", "person:s1", "s1")
    assert asks.wake_due([*held, woke, wrote, newer], "s1") == newer


def test_a_wake_after_the_last_sign_off_leaves_nothing_due():
    start = stamped("hand-start", "2026-11-19T18:00:00.000Z", "agent:hand")
    answer = stamped("question", "2026-11-19T18:05:00.000Z", "person:s1", "abc")
    wake1 = stamped("wake", "2026-11-19T18:06:00.000Z", "agent:captain")
    cap = stamped("review-cap", "2026-11-19T18:10:00.000Z", "person:s1", "s1")
    wake2 = stamped("wake", "2026-11-19T18:11:00.000Z", "agent:captain")
    finding = stamped("finding", "2026-11-19T18:15:00.000Z", "person:s1", "f1")
    cap2 = stamped("review-cap", "2026-11-19T18:16:00.000Z", "person:s1", "s1")
    wake3 = stamped("wake", "2026-11-19T18:17:00.000Z", "agent:captain")
    sign = stamped("sign-off", "2026-11-19T18:20:00.000Z", "person:s1", "s1")
    wake4 = stamped("wake", "2026-11-19T18:21:00.000Z", "agent:captain")
    held = [start, answer, wake1, cap, wake2, finding, cap2, wake3, sign]
    assert asks.wake_due(held, "s1") == sign
    assert asks.wake_due([*held, wake4], "s1") is None


def test_wake_due_ignores_stories_without_a_hand_and_decisions_a_hand_does_not_wait_on():
    tier = stamped("tier", "2026-11-19T10:10:00.000Z", "person:s1", "s1")
    assert asks.wake_due([tier], "s1") is None
    assert asks.wake_due([stamped("question", "2026-11-19T10:10:00.000Z", "person:s1")], "s1") is None
    start = stamped("hand-start", "2026-11-19T10:00:00.000Z", "agent:hand")
    assert asks.wake_due([start, tier, stamped("sitting", "2026-11-19T10:11:00.000Z", "person:s1", "s1")], "s1") is None
    edit = stamped("config-edit-request", "2026-11-19T10:12:00.000Z", "agent:hand")
    assert asks.wake_due([start, tier, edit], "s1") is None


def test_wake_due_only_for_a_decision_this_story_s_hand_waits_on():
    start = stamped("hand-start", "2026-11-19T10:00:00.000Z", "agent:hand", "s1")
    feature_round = Record("review-round", "p1:feature:1", "r1", "agent:review", {"findings": [{"id": "f1"}]})
    story_round = Record("review-round", "s1:plan:1", "r2", "agent:review", {"findings": [{"id": "f2"}]})
    rounds = [start, feature_round, story_round]
    on_feature = stamped("finding", "2026-11-19T10:10:00.000Z", "person:s1", "f1")
    assert asks.wake_due([*rounds, on_feature], "s1") is None
    on_story = stamped("finding", "2026-11-19T10:10:00.000Z", "person:s1", "f2")
    assert asks.wake_due([*rounds, on_story], "s1") is None
    proposed = asks.proposal_record("f2", "r2", "noise")
    assert asks.wake_due([*rounds, proposed, on_story], "s1") == on_story
    unasked = stamped("question", "2026-11-19T10:10:00.000Z", "person:s1", "abc")
    assert asks.wake_due([start, unasked], "s1") is None
    elsewhere = stamped("sign-off", "2026-11-19T10:10:00.000Z", "person:s1", "s2")
    assert asks.wake_due([start, elsewhere], "s1") is None


def test_a_proposal_on_a_p1_waived_at_the_cap_is_no_longer_open():
    p1 = review.parse_findings(
        json.dumps(
            [
                {
                    "lens": "correctness",
                    "severity": "P1",
                    "path": "a.py",
                    "line": 1,
                    "summary": "Late.",
                    "fix": "f",
                    "evidence": "e",
                    "confidence": 0.9,
                    "changes_delivery": True,
                }
            ]
        )
    )
    rounds = [
        review.round_record("s1", "branch", review.RoundPlan(n, n > 1, ""), f"h{n}", p1, f"c{n}") for n in (1, 2, 3)
    ]
    finding = rounds[0].data["findings"][0]["id"]
    proposal = asks.proposal_record(finding, "h1", "out of scope")
    proceed = Record(
        review.CAP_DECISION,
        review.cap_subject(rounds[-1]),
        review.cap_version(rounds[-1]),
        "person:pat",
        {"answer": review.PROCEED},
    )
    assert asks.open_proposals([*rounds, proposal]) == [proposal]
    assert asks.open_proposals([*rounds, proposal, proceed]) == []


def test_open_proposals_are_those_without_a_person_decision():
    proposals = [asks.proposal_record("f1", "v1", "noise"), asks.proposal_record("f2", "v1", "noise")]
    decision = Record("finding", "f1", "v1", "person:s1", {"answer": "Accept"})
    assert [r.subject for r in asks.open_proposals([*proposals, decision])] == ["f2"]


def test_a_proposal_holds_its_answer_and_defaults_to_decline():
    accept = asks.proposal_record("f1", "v1", "in scope", "Accept")
    assert (accept.kind, accept.data) == ("decline-proposal", {"reason": "in scope", "answer": "Accept"})
    assert asks.proposal_record("f1", "v1", "noise").data["answer"] == "Decline"
    legacy = Record(asks.PROPOSAL, "f2", "v1", "agent:review", {"reason": "old"})
    assert asks.proposed_answer(legacy) == "Decline"


def test_proposed_is_the_newest_open_proposal_for_a_finding():
    first = asks.proposal_record("f1", "v1", "noise")
    second = asks.proposal_record("f1", "v1", "in scope", "Accept")
    other = asks.proposal_record("f2", "v1", "noise")
    assert asks.proposed([first, second, other], "f1") == second
    assert asks.proposed([first, second, other], "f3") is None
    decision = Record("finding", "f1", "v1", "person:s1", {"answer": "Accept"})
    assert asks.proposed([first, second, decision], "f1") is None
    assert asks.open_proposals([first, second, other]) == [second, other]
