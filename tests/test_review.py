from __future__ import annotations

import json

import pytest

from deckhand import review
from deckhand.review import RoundPlan, Stop
from deckhand.step import Refusal


def raw(**over) -> dict:
    item = {
        "lens": "correctness",
        "severity": "P2",
        "path": "plan.md",
        "line": 3,
        "summary": "A step contradicts a criterion.",
        "fix": "Reorder the steps.",
        "evidence": "plan.md:3 with an empty cart",
        "confidence": 0.9,
        "reason": "the evidence holds",
    }
    return item | over


def findings(*items: dict) -> list[review.Finding]:
    return review.parse_findings(json.dumps(list(items)))


def done(n: int, target: str, *items: dict, stage: str = "plan"):
    plan = RoundPlan(n, scoped=n > 1, base_hash=f"h{n - 1}" if n > 1 else "")
    return review.round_record("s1", stage, plan, target, findings(*items))


def test_unchanged_text_stops():
    rounds = [done(1, "h1", raw(severity="P1"))]
    assert review.next_round(rounds, "h1", set()) == Stop("unchanged")


def test_unchanged_wins_over_clean_and_cap():
    rounds = [done(1, "h1"), done(2, "h2"), done(3, "h3", raw(severity="P1"))]
    assert review.next_round(rounds, "h3", set()) == Stop("unchanged")


def test_first_round_is_not_scoped():
    assert review.next_round([], "h1", set()) == RoundPlan(1, scoped=False, base_hash="")


def test_no_accepted_p1_stops():
    rounds = [done(1, "h1", raw(severity="P2"), raw(severity="P3", summary="other"))]
    assert review.next_round(rounds, "h2", set()) == Stop("clean")


def test_declined_p1_is_not_accepted():
    p1 = raw(severity="P1")
    rounds = [done(1, "h1", p1)]
    declined = {findings(p1)[0].id}
    assert review.next_round(rounds, "h2", declined) == Stop("clean")
    assert review.next_round(rounds, "h2", {"other"}) == RoundPlan(2, scoped=True, base_hash="h1")


def test_cap_after_three():
    p1 = raw(severity="P1")
    rounds = [done(1, "h1", p1), done(2, "h2", p1), done(3, "h3", p1)]
    assert review.next_round(rounds, "h4", set()) == Stop("cap")


def test_revision_is_scoped():
    p1 = raw(severity="P1")
    rounds = [done(1, "h1", p1), done(2, "h2", p1)]
    assert review.next_round(rounds, "h3", set()) == RoundPlan(3, scoped=True, base_hash="h2")


def test_round_record_shape():
    record = done(2, "h2", raw())
    assert (record.kind, record.subject, record.version) == ("review-round", "s1:plan:2", "h2")
    assert record.data["target_hash"] == "h2"
    assert record.data["scoped"] is True
    assert record.data["base_hash"] == "h1"
    assert record.data["findings"][0]["summary"] == "A step contradicts a criterion."


def test_doubtful_folded_last():
    items = findings(
        raw(summary="doubtful", confidence=0.4),
        raw(summary="low", confidence=0.5),
        raw(summary="high", confidence=0.95),
    )
    assert [f.summary for f in review.ordered(items)] == ["high", "low", "doubtful"]


def test_ordered_keeps_every_finding_and_ties_keep_order():
    items = findings(raw(summary="a", confidence=0.7), raw(summary="b", confidence=0.7))
    assert [f.summary for f in review.ordered(items)] == ["a", "b"]


def test_finding_id_is_stable_and_depends_on_identity():
    a = findings(raw())[0]
    assert a.id == findings(raw(confidence=0.1, fix="else", evidence="else"))[0].id
    assert len(a.id) == 12
    assert a.id != findings(raw(line=4))[0].id


def test_empty_list_is_clean():
    assert review.parse_findings("[]") == []


@pytest.mark.parametrize(
    ("text", "where"),
    [
        ("not json", "JSON"),
        ('{"a": 1}', "list"),
        ("[1]", "finding 0"),
        (json.dumps([raw(), raw(severity="P4")]), "finding 1: severity"),
        (json.dumps([raw(path=3)]), "finding 0: path"),
        (json.dumps([raw(line="3")]), "finding 0: line"),
        (json.dumps([raw(line=True)]), "finding 0: line"),
        (json.dumps([raw(line=-1)]), "finding 0: line"),
        (json.dumps([raw(confidence=1.5)]), "finding 0: confidence"),
        (json.dumps([raw(confidence="high")]), "finding 0: confidence"),
        (json.dumps([{k: v for k, v in raw().items() if k != "fix"}]), "finding 0: fix"),
    ],
)
def test_bad_finding_json_refuses(text, where):
    with pytest.raises(Refusal, match=where):
        review.parse_findings(text)


def test_rounds_in_any_order_use_the_latest():
    p1 = raw(severity="P1")
    rounds = [done(2, "h2", p1), done(1, "h1", p1)]
    assert review.next_round(rounds, "h2", set()) == Stop("unchanged")
