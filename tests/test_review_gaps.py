from __future__ import annotations

from deckhand import asks, review, review_gaps
from deckhand.records import Record
from deckhand.review import RoundPlan
from tests.test_review import done, findings, id_of, proceed_at, raw


def test_a_delivery_finding_without_a_person_decision_is_undecided():
    delivery = raw(changes_delivery=True)
    records = [done(1, "h1", delivery), done(2, "h2")]
    assert [f["id"] for f in review_gaps.undecided_delivery(records, "s1", "plan")] == [id_of(delivery)]
    assert review_gaps.undecided_delivery(records, "s1", "branch") == []
    hand = review.accept_record(id_of(delivery), "h1")
    assert review_gaps.undecided_delivery([*records, hand], "s1", "plan") != []
    person = Record(review.DECISION, id_of(delivery), "h1", "person:pat", {"answer": review.ACCEPT})
    assert review_gaps.undecided_delivery([*records, person], "s1", "plan") == []


def test_a_delivery_p1_waived_at_the_cap_is_no_longer_undecided():
    delivery = raw(severity="P1", changes_delivery=True)
    rounds = [done(n, f"h{n}", delivery) for n in (1, 2, 3)]
    records = [*rounds, proceed_at(rounds[-1])]
    assert review_gaps.undecided_delivery(records, "s1", "plan") == []
    assert review_gaps.delivery_gaps(records, "s1", "plan", "ACME-1") == []
    assert review_gaps.undecided_delivery(rounds, "s1", "plan") != []


def test_delivery_gaps_split_by_who_acts():
    delivery = raw(changes_delivery=True)
    records = [done(1, "h1", delivery)]
    finding = id_of(delivery)
    [gap] = review_gaps.delivery_gaps(records, "s1", "plan", "ACME-1")
    assert gap.hand == (
        f"{finding} changes what the story delivers; propose an answer with review apply ACME-1 resolve --stage plan"
        f" --propose-accept {finding} or --propose-decline {finding} --reason TEXT"
    )
    assert gap.person == f"finding {finding} waits for the hand"
    proposal = Record(review.PROPOSAL, finding, "h1", "agent:review", {"reason": "r", "answer": review.ACCEPT})
    [waiting] = review_gaps.delivery_gaps([*records, proposal], "s1", "plan", "ACME-1")
    assert waiting.hand == f"{finding} waits for the person's decision"
    person = Record(review.DECISION, finding, "h1", "person:pat", {"answer": review.ACCEPT})
    assert review_gaps.delivery_gaps([*records, proposal, person], "s1", "plan", "ACME-1") == []


def test_feature_gaps_need_the_current_target_at_any_count():
    def feature(n: int, target: str) -> Record:
        return review.round_record("p1", "feature", RoundPlan(n, n > 1, ""), target, [])

    assert review_gaps.feature_gaps("p1", [feature(1, "h1")], "h1", "p1") == []
    capped = [feature(n, f"h{n}") for n in (1, 2, 3)]
    [gap] = review_gaps.feature_gaps("p1", capped, "h4", "p1")
    assert gap.hand == "the feature review has not reviewed the current contract and stories; run a feature round"


def test_a_left_open_p3_is_the_hands_to_resolve():
    left = raw(severity="P3")
    records = [done(1, "h1", left, stage="branch"), done(2, "h2", stage="branch")]
    [gap] = review_gaps.left_open_gaps(records, "s1", "branch", "ACME-1")
    assert "review apply ACME-1 resolve --stage branch --accept" in gap.hand
    assert "--propose-decline" in gap.hand
    assert gap.person == f"finding {id_of(left, 'branch')} waits for the hand"


def test_a_proposed_left_open_finding_waits_for_the_person():
    left = raw(severity="P3")
    records = [done(1, "h1", left, stage="branch"), done(2, "h2", stage="branch")]
    finding = id_of(left, "branch")
    proposal = Record(review.PROPOSAL, finding, "h1", "agent:review", {"reason": "r", "answer": review.DECLINE})
    [gap] = review_gaps.left_open_gaps([*records, proposal], "s1", "branch", "ACME-1")
    assert gap.hand == f"{finding} waits for the person's decision"
    assert gap.person == f"finding {finding} was left open by the hand; decide it"


def feature_round(n: int, target: str, *items: dict) -> Record:
    return review.round_record("p1", "feature", RoundPlan(n, n > 1, ""), target, findings(*items))


def test_a_feature_p2_that_changes_delivery_blocks_approval_until_decided():
    delivery = raw(changes_delivery=True)
    finding = id_of(delivery, "feature")
    records = [feature_round(1, "h1", delivery)]
    [gap] = review_gaps.feature_gaps("p1", records, "h1", "p1")
    assert gap.id == finding
    assert "feature-review apply resolve --propose-accept" in gap.hand
    proposal = asks.proposal_record(finding, records[0].version, "out of scope")
    [waiting] = review_gaps.feature_gaps("p1", [*records, proposal], "h1", "p1")
    assert waiting.person == f"finding {finding} changes what the feature delivers; decide it"
    assert waiting.hand == f"{finding} waits for the person: decide context finding p1 --finding {finding}"
    decline = Record(review.DECISION, finding, "h1", "person:pat", {"answer": review.DECLINE})
    assert review_gaps.feature_gaps("p1", [*records, proposal, decline], "h1", "p1") == []


def test_a_feature_p1_proceeded_at_the_cap_no_longer_blocks():
    p1 = raw(severity="P1")
    rounds = [feature_round(n, f"h{n}", p1) for n in (1, 2, 3)]
    [capped] = review_gaps.feature_gaps("p1", rounds, "h3", "p1")
    assert capped.id == id_of(p1, "feature")
    assert capped.hand.endswith("decide context review-cap p1 --stage feature")
    assert capped.person == f"the feature review holds an open P1 {capped.id}: {p1['summary']}"
    records = [*rounds, proceed_at(rounds[-1])]
    assert review_gaps.feature_gaps("p1", records, "h3", "p1") == []
    prefix = "p1:feature"
    held, _ = review.counted(records, prefix)
    since = review.proceeded(records, prefix)
    assert review.next_round(held, "h4", review.excused(records, prefix), since=since) == RoundPlan(4, True, "h3")
    assert review.next_round(held, "h4", review.excused(rounds, prefix)) == review.Stop("cap")


def test_an_open_feature_p1_tells_the_captain_to_fix_or_propose():
    p1 = raw(severity="P1")
    records = [feature_round(1, "h1", p1)]
    [gap] = review_gaps.feature_gaps("p1", records, "h1", "p1")
    assert gap.hand.endswith(f"feature-review apply resolve --propose-decline {gap.id} --reason TEXT")
    proposal = asks.proposal_record(gap.id, records[0].version, "out of scope")
    [waiting] = review_gaps.feature_gaps("p1", [*records, proposal], "h1", "p1")
    assert waiting.hand.endswith(f"decide context finding p1 --finding {gap.id}")
    decline = Record(review.DECISION, gap.id, "h1", "person:pat", {"answer": review.DECLINE})
    assert review_gaps.feature_gaps("p1", [*records, proposal, decline], "h1", "p1") == []


def test_a_feature_p1_the_person_kept_is_only_fixed():
    p1 = raw(severity="P1")
    records = [feature_round(1, "h1", p1)]
    finding = id_of(p1, "feature")
    proposal = asks.proposal_record(finding, records[0].version, "out of scope")
    kept = Record(review.DECISION, finding, "h1", "person:pat", {"answer": review.ACCEPT})
    [gap] = review_gaps.feature_gaps("p1", [*records, proposal, kept], "h1", "p1")
    assert gap.hand == f"{gap.person}; fix it and review again"


def test_feature_gap_person_text_names_no_action_for_the_person():
    old = review.round_record("p1", "feature", RoundPlan(1, False, ""), "h0", [])
    [gap] = review_gaps.feature_gaps("p1", [old], "h1", "p1")
    assert gap.person == "the feature review has not reviewed the current contract and stories; a feature review is due"
    assert gap.hand.endswith("run a feature round")
