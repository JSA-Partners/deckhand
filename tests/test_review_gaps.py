from __future__ import annotations

from deckhand import review, review_gaps
from deckhand.records import Record
from deckhand.review import RoundPlan
from tests.test_review import done, id_of, proceed_at, raw


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
    assert review_gaps.delivery_gaps(records, "s1", "plan") == []
    assert review_gaps.undecided_delivery(rounds, "s1", "plan") != []


def test_delivery_gaps_split_by_who_acts():
    delivery = raw(changes_delivery=True)
    records = [done(1, "h1", delivery)]
    finding = id_of(delivery)
    assert review_gaps.delivery_gaps(records, "s1", "plan", "ACME-1") == [
        f"{finding} changes what the story delivers; propose an answer with review apply ACME-1 resolve --stage plan"
        f" --propose-accept {finding} or --propose-decline {finding} --reason TEXT"
    ]
    proposal = Record(review.PROPOSAL, finding, "h1", "agent:review", {"reason": "r", "answer": review.ACCEPT})
    assert review_gaps.delivery_gaps([*records, proposal], "s1", "plan") == [
        f"{finding} waits for the person's decision"
    ]
    person = Record(review.DECISION, finding, "h1", "person:pat", {"answer": review.ACCEPT})
    assert review_gaps.delivery_gaps([*records, proposal, person], "s1", "plan") == []


def test_feature_gaps_need_the_current_target_at_any_count():
    def feature(n: int, target: str) -> Record:
        return review.round_record("p1", "feature", RoundPlan(n, n > 1, ""), target, [])

    assert review_gaps.feature_gaps("p1", [feature(1, "h1")], "h1") == []
    capped = [feature(n, f"h{n}") for n in (1, 2, 3)]
    assert review_gaps.feature_gaps("p1", capped, "h4") == [
        "the feature review has not reviewed the current contract and stories; run a feature round"
    ]
