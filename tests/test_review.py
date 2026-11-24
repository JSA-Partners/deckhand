from __future__ import annotations

import json

import pytest

from deckhand import review
from deckhand.records import Record
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


def done(n: int, target: str, *items: dict, stage: str = "plan", head: str = "", carried: tuple[str, ...] = ()):
    plan = RoundPlan(n, scoped=n > 1, base_hash=f"h{n - 1}" if n > 1 else "")
    return review.round_record("s1", stage, plan, target, findings(*items), head, list(carried))


def id_of(item: dict, stage: str = "plan") -> str:
    return review.finding_id(findings(item)[0], stage)


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
    assert review.next_round(rounds, "h1", set()) == Stop("clean")


def test_revision_after_a_clean_round_is_scoped():
    rounds = [done(1, "h1", raw(severity="P2"))]
    assert review.next_round(rounds, "h2", set()) == RoundPlan(2, scoped=True, base_hash="h1")


def test_a_commit_after_a_clean_third_round_gets_a_scoped_round():
    rounds = [done(1, "h1", raw(severity="P1")), done(2, "h2", raw(severity="P1")), done(3, "h3")]
    assert review.next_round(rounds, "h3", set()) == Stop("clean")
    assert review.next_round(rounds, "h4", set()) == RoundPlan(4, scoped=True, base_hash="h3")
    assert review.cleared(rounds, "s1", "plan", "h3")
    assert not review.cleared(rounds, "s1", "plan", "h4")


def test_declined_p1_is_not_accepted():
    p1 = raw(severity="P1")
    rounds = [done(1, "h1", p1)]
    declined = {id_of(p1)}
    assert review.next_round(rounds, "h1", declined) == Stop("clean")
    assert review.next_round(rounds, "h1", {"other"}) == Stop("unchanged")
    assert review.next_round(rounds, "h2", declined) == RoundPlan(2, scoped=True, base_hash="h1")


def test_rounds_without_a_p1_never_count_to_the_cap():
    rounds = [done(n, f"h{n}", raw(severity="P2", summary=f"Gap {n}.")) for n in (1, 2, 3, 4)]
    assert review.p1_rounds(rounds, set()) == 0
    assert review.next_round(rounds, "h5", set()) == RoundPlan(5, scoped=True, base_hash="h4")


def test_three_p1_rounds_stop_at_the_cap_whatever_the_target():
    p1 = raw(severity="P1")
    rounds = [done(n, f"h{n}", p1) for n in (1, 2, 3)]
    assert review.next_round(rounds, "h4", set()) == Stop("cap")
    assert review.at_cap(rounds, set())
    assert not review.cleared(rounds, "s1", "plan", "h3")


def proceed_at(round: Record) -> Record:
    return Record(review.CAP_DECISION, "s1", review.cap_version(round), "person:pat", {"answer": review.PROCEED})


def test_proceed_waives_the_open_p1_and_the_next_commit_is_still_reviewed():
    p1 = raw(severity="P1")
    rounds = [done(n, f"h{n}", p1) for n in (1, 2, 3)]
    records = [*rounds, proceed_at(rounds[-1])]
    assert review.waived(records, "s1:plan") == {id_of(p1)}
    excused = review.excused(records, "s1:plan")
    assert review.cleared(records, "s1", "plan", "h3")
    assert review.next_round(rounds, "h4", excused) == RoundPlan(4, scoped=True, base_hash="h3")
    assert not review.cleared(records, "s1", "plan", "h4")


def test_a_new_p1_after_proceed_blocks_until_fixed_and_starts_a_fresh_count():
    p1, other = raw(severity="P1"), raw(severity="P1", summary="A new hole.")
    rounds = [done(n, f"h{n}", p1) for n in (1, 2, 3)]
    fourth = done(4, "h4", other)
    records = [*rounds, proceed_at(rounds[-1]), fourth]
    excused = review.excused(records, "s1:plan")
    assert not review.cleared(records, "s1", "plan", "h4")
    assert review.p1_rounds([*rounds, fourth], excused) == 1
    assert review.next_round([*rounds, fourth], "h5", excused) == RoundPlan(5, scoped=True, base_hash="h4")


def test_proceed_restarts_the_count_of_p1_rounds():
    raised = [raw(severity="P1", summary=f"Hole {name}.") for name in ("W", "V", "U", "T")]
    rounds = [done(n, f"h{n}", raised[n - 1]) for n in (1, 2, 3)]
    fourth = done(4, "h4", raised[3])
    records = [*rounds, proceed_at(rounds[-1]), fourth]
    assert review.waived(records, "s1:plan") == {id_of(raised[2])}
    excused = review.excused(records, "s1:plan")
    since = review.proceeded(records, "s1:plan")
    assert since == 3
    assert review.p1_rounds([*rounds, fourth], excused, since) == 1
    assert not review.at_cap([*rounds, fourth], excused, since)
    assert review.next_round([*rounds, fourth], "h5", excused, since=since) == RoundPlan(5, scoped=True, base_hash="h4")


def test_at_the_cap_a_moved_head_with_the_same_diff_stops_at_the_cap():
    p1 = raw(severity="P1")
    rounds = [done(n, f"h{n}", p1, stage="branch", head=f"c{n}") for n in (1, 2, 3)]
    assert review.next_round(rounds, "h3", set(), "c4") == Stop("cap")
    assert review.next_round(rounds, "h3", set(), "c3") == Stop("unchanged")


def test_a_waived_p1_is_never_carried_into_the_next_round():
    p1 = raw(severity="P1")
    rounds = [done(n, f"h{n}", p1) for n in (1, 2, 3)]
    assert [f["id"] for f in review.carried(rounds, "s1:plan")] == [id_of(p1)]
    assert review.carried([*rounds, proceed_at(rounds[-1])], "s1:plan") == []


def test_an_accepted_finding_at_the_old_cap_count_is_carried_then_dropped_as_fixed():
    p2 = raw(severity="P2")
    rounds = [done(1, "h1", p2), done(2, "h2", p2), done(3, "h3", p2)]
    records = [*rounds, person(id_of(p2), version="h3")]
    assert review.next_round(rounds, "h4", set()) == RoundPlan(4, scoped=True, base_hash="h3")
    assert [f["id"] for f in review.carried(records, "s1:plan")] == [id_of(p2)]
    fourth = done(4, "h4", carried=(id_of(p2),))
    assert review.unfixed([*records, fourth]) == []


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


def test_finding_id_is_stable_and_depends_on_identity_and_stage():
    a = id_of(raw())
    assert a == id_of(raw(confidence=0.1, fix="else", evidence="else"))
    assert len(a) == 12
    assert a != id_of(raw(line=4))
    assert a != id_of(raw(), "branch")
    assert done(1, "h1", raw(), stage="branch").data["findings"][0]["id"] == id_of(raw(), "branch")


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


def test_a_new_head_with_the_same_diff_carries_the_last_round():
    rounds = [done(1, "h1", raw(severity="P1"), stage="branch", head="c1")]
    assert review.next_round(rounds, "h1", set(), "c1") == Stop("unchanged")
    assert review.next_round(rounds, "h1", set(), "c2") == RoundPlan(2, scoped=True, base_hash="h1", carry=True)
    assert review.next_round(rounds, "h2", set(), "c2") == RoundPlan(2, scoped=True, base_hash="h1")


def test_a_carried_round_keeps_the_findings_at_the_new_head_and_never_counts_to_the_cap():
    p1 = raw(severity="P1")
    rounds = [done(n, f"h{n}", p1, stage="branch", head=f"c{n}") for n in (1, 2, 3)]
    assert review.next_round(rounds, "h3", set(), "c4") == Stop("cap")
    plan = review.next_round(rounds, "h3", {id_of(p1, "branch")}, "c4")
    assert plan == RoundPlan(4, scoped=True, base_hash="h3", carry=True)
    carried = review.carry_record("s1", "branch", plan, rounds[-1], "c4")
    assert (carried.subject, carried.version) == ("s1:branch:4", "h3")
    assert carried.data["findings"] == rounds[-1].data["findings"]
    assert (carried.data["head"], carried.data["carry"]) == ("c4", True)
    rounds.append(carried)
    assert review.at_cap(rounds, set())
    assert not review.at_cap(rounds, {id_of(p1, "branch")})
    early = [done(1, "h1", p1, stage="branch", head="c1")]
    early.append(review.carry_record("s1", "branch", RoundPlan(2, True, "h1", carry=True), early[0], "c2"))
    early.append(done(3, "h3", p1, stage="branch", head="c3"))
    assert review.next_round(early, "h4", set(), "c4") == RoundPlan(4, scoped=True, base_hash="h3")


def test_locate_finds_a_finding_of_an_earlier_round():
    first, second = raw(summary="first"), raw(summary="second")
    records = [done(1, "h1", first), done(2, "h2", second)]
    found = review.locate(records, id_of(first))
    assert found is not None and found[0].subject == "s1:plan:1"


def test_a_delivery_finding_without_a_person_decision_is_undecided():
    delivery = raw(changes_delivery=True)
    records = [done(1, "h1", delivery), done(2, "h2")]
    assert [f["id"] for f in review.undecided_delivery(records, "s1", "plan")] == [id_of(delivery)]
    assert review.undecided_delivery(records, "s1", "branch") == []
    hand = review.accept_record(id_of(delivery), "h1")
    assert review.undecided_delivery([*records, hand], "s1", "plan") != []
    person = Record(review.DECISION, id_of(delivery), "h1", "person:pat", {"answer": review.ACCEPT})
    assert review.undecided_delivery([*records, person], "s1", "plan") == []


def test_feature_gaps_need_the_current_target_at_any_count():
    def feature(n: int, target: str) -> Record:
        return review.round_record("p1", "feature", RoundPlan(n, n > 1, ""), target, [])

    assert review.feature_gaps("p1", [feature(1, "h1")], "h1") == []
    capped = [feature(n, f"h{n}") for n in (1, 2, 3)]
    assert review.feature_gaps("p1", capped, "h4") == [
        "the feature review has not reviewed the current contract and stories; run a feature round"
    ]


def person(finding: str, answer: str = review.ACCEPT, version: str = "h1") -> Record:
    return Record(review.DECISION, finding, version, "person:pat", {"answer": answer})


FIRST, SECOND = raw(summary="first"), raw(summary="second")


def summaries(records: list[Record]) -> list[str]:
    return [f["summary"] for f in review.unfixed(records)]


def test_an_accepted_finding_with_no_later_round_is_unfixed():
    records = [done(1, "h1", FIRST, SECOND), person(id_of(SECOND)), person(id_of(FIRST))]
    assert summaries(records) == ["first", "second"]


def test_a_scoped_round_that_did_not_carry_an_accepted_finding_leaves_it_unfixed():
    records = [done(1, "h1", FIRST, SECOND), done(2, "h2", SECOND), person(id_of(FIRST)), person(id_of(SECOND))]
    assert summaries(records) == ["first", "second"]


def test_an_accepted_finding_a_later_round_carried_and_no_longer_raises_is_fixed():
    later = done(2, "h2", SECOND, carried=(id_of(FIRST), id_of(SECOND)))
    records = [done(1, "h1", FIRST, SECOND), later, person(id_of(FIRST)), person(id_of(SECOND))]
    assert summaries(records) == ["second"]


def test_an_accepted_finding_a_later_round_carried_and_raised_again_is_unfixed():
    records = [done(1, "h1", FIRST), done(2, "h2", FIRST, carried=(id_of(FIRST),)), person(id_of(FIRST))]
    assert summaries(records) == ["first"]


def test_carried_lists_the_open_p1s_and_the_accepted_findings_not_yet_fixed():
    p1, p2, p3 = raw(severity="P1", summary="p1"), raw(summary="p2"), raw(severity="P3", summary="p3")
    records = [done(1, "h1", p1, p2, p3), person(id_of(p2)), person(id_of(p1))]
    assert [f["summary"] for f in review.carried(records, "s1:plan")] == ["p1", "p2"]
    assert done(2, "h2", carried=(id_of(p1),)).data["carried"] == [id_of(p1)]
    assert "carried" not in done(2, "h2").data


def test_a_declined_or_undecided_finding_is_never_unfixed():
    records = [done(1, "h1", FIRST, SECOND), person(id_of(FIRST), review.DECLINE)]
    assert review.unfixed(records) == []


def test_an_accepted_finding_an_unscoped_later_round_does_not_raise_is_fixed():
    whole = review.round_record("s1", "plan", RoundPlan(4, scoped=False, base_hash=""), "h4", findings(SECOND))
    records = [done(3, "h3", FIRST, SECOND), whole, person(id_of(FIRST)), person(id_of(SECOND))]
    assert summaries(records) == ["second"]


def test_touches_is_optional_and_kept():
    with_line = findings(raw(changes_delivery=True, touches="Contract: greets in English only"))[0]
    assert with_line.touches == "Contract: greets in English only"
    [kept] = review.round_record("s1", "plan", RoundPlan(1, scoped=False, base_hash=""), "h1", [with_line]).data[
        "findings"
    ]
    assert kept["touches"] == "Contract: greets in English only"
    assert findings(raw(changes_delivery=True))[0].touches == ""
    with pytest.raises(Refusal, match="touches"):
        findings(raw(touches=3))
