from __future__ import annotations

from deckhand import review, signoff, wakes
from deckhand.records import Record
from deckhand.store.state import Message
from tests.test_review import done, id_of, person, raw

STORY = {"id": "s1", "identifier": "ACME-1", "title": "Checkout"}
OPEN = raw(path="cart.py", line=12, summary="The total skips tax.", fix="Add the tax line.")
FIXED = raw(path="cart.py", line=30, summary="A stale cache.", fix="Clear it.")


def not_yet(**extra: str) -> Record:
    return Record(signoff.KIND, "s1:n1", "c2", "person:pat", {"answer": "Not yet"} | extra)


def accepted_two_fixed_one() -> list[Record]:
    records = [done(1, "h1", OPEN, FIXED, stage="branch")]
    records += [person(id_of(OPEN, "branch")), person(id_of(FIXED, "branch"))]
    return [*records, review.accept_record(id_of(FIXED, "branch"), "h1")]


def test_a_not_yet_body_carries_each_accepted_finding_with_its_fix():
    assert wakes.body(STORY, not_yet(), accepted_two_fixed_one()).splitlines() == [
        "The person decided sign-off: Not yet",
        "Accepted findings still to fix:",
        "- P2 correctness cart.py:12 The total skips tax. Fix: Add the tax line.",
    ]


def test_a_body_carries_the_reason_when_the_record_has_one():
    assert wakes.body(STORY, not_yet(note="Tax first."), []).splitlines() == [
        "The person decided sign-off: Not yet",
        "Reason: Tax first.",
    ]


def test_a_finding_body_carries_the_finding_line_and_its_fix():
    records = [done(1, "h1", OPEN, stage="branch")]
    decision = Record(review.DECISION, id_of(OPEN, "branch"), "h1", "person:pat", {"answer": review.DECLINE})
    assert wakes.body(STORY, decision, records).splitlines() == [
        "The person decided finding: Decline",
        "P2 correctness cart.py:12 The total skips tax.",
        "Fix: Add the tax line.",
    ]


def test_nudge_and_sync_bodies():
    assert wakes.nudge_body() == "Carry on from the first step the contexts show undone."
    assert wakes.sync_body({"identifier": "ACME-2"}) == "ACME-2 merged. Bring main in before your next commit."


def message(seq: int, body: str) -> Message:
    return Message(seq, f"k{seq}", "hand-ACME-1", wakes.NUDGE, body, "2026-11-23T10:00:00.000Z", None, None)


def test_prompt_orders_bodies_by_seq_and_ends_with_the_ack_line():
    text = wakes.prompt([message(5, "second"), message(2, "first")])
    assert text == "first\n\nsecond\n\nThen acknowledge with deckhand mail apply."
