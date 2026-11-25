from __future__ import annotations

import json

import pytest

from deckhand import asks, features, pins, review
from deckhand.records import Record
from deckhand.step import Refusal
from tests.test_step_decide import feature_store


def asked():
    store = feature_store()
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    story = features.story(store, feature, "ACME-1")
    store.put_record(story["id"], asks.question_record(story["id"], asks.parse_question(_QUESTION)))
    store.put_record(story["id"], asks.edit_record(story["id"], asks.parse_edit(_EDIT)))
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    return store, feature


_QUESTION = '{"question": "Which queue?", "options": ["Redis", "SQS"], "why": "Both fit."}'
_EDIT = '{"path": ".claude/settings.json", "change": "allow gh pr list", "why": "the hand lists pull requests"}'
QID = asks.question_id("Which queue?")
P1 = {
    "lens": "correctness",
    "severity": "P1",
    "path": "a.py",
    "line": 1,
    "summary": "A hole.",
    "fix": "Fill it.",
    "evidence": "a.py:1",
    "confidence": 0.9,
}
P2 = P1 | {"severity": "P2"}


def test_question_pin_holds_the_records_options_in_order():
    store, feature = asked()
    held = pins.pin(store, feature, "question", QID, ["ACME-1"])
    assert (held.subject, held.options, held.stories) == (QID, ["Redis", "SQS"], ["s1"])
    assert "Which queue?" in held.detail


@pytest.mark.parametrize(
    "stories,match",
    [
        ([], "question needs exactly one --story"),
        (["ACME-1", "ACME-2"], "question needs exactly one --story"),
        (["ACME-2"], "no question QID on ACME-2"),
    ],
)
def test_question_pin_refuses(stories: list[str], match: str):
    store, feature = asked()
    with pytest.raises(Refusal, match=match.replace("QID", QID)):
        pins.pin(store, feature, "question", QID, stories)


def test_config_edit_pin_takes_an_item_id_and_names_the_edit():
    store, feature = asked()
    [record] = [r for r in store.records("s1") if r.kind == "config-edit-request"]
    item = record.subject.rpartition(":")[2]
    held = pins.pin(store, feature, "config-edit", "ACME-1", [], item=item)
    assert held.subject == item
    assert ".claude/settings.json" in held.detail and "allow gh pr list" in held.detail
    with pytest.raises(Refusal, match="config-edit needs --item ID"):
        pins.pin(store, feature, "config-edit", "ACME-1", [])
    with pytest.raises(Refusal, match="no config edit 000000000000"):
        pins.pin(store, feature, "config-edit", "ACME-1", [], item="000000000000")


def test_guards_still_hold_for_the_other_kinds():
    store, feature = asked()
    with pytest.raises(Refusal, match="--story applies only to decision-item and question"):
        pins.pin(store, feature, "contract", "p", ["ACME-1"])
    with pytest.raises(Refusal, match="--item applies only to quiz, after-merge, config-edit"):
        pins.pin(store, feature, "contract", "p", [], item="1")
    with pytest.raises(Refusal, match="quiz needs --item N"):
        pins.pin(store, feature, "quiz", "ACME-1", [])
    with pytest.raises(Refusal, match="--item takes an item number from 1"):
        pins.pin(store, feature, "quiz", "ACME-1", [], item="x")


def capped(store, *items: dict) -> Record:
    found = review.parse_findings(json.dumps(list(items)))
    for n in (1, 2, 3):
        plan = review.RoundPlan(n, scoped=n > 1, base_hash="")
        store.put_record("s1", review.round_record("s1", "branch", plan, f"hash{n}", found, f"c{n}"))
    return review.last(review.rounds_of(store.records("s1"), "s1:branch"))


def test_cap_pin_is_pinned_to_the_round_whatever_head_is_passed():
    store, _ = asked()
    latest = capped(store, P1)
    feature = features.open_feature(store, "ACME")
    for head in ("", "c3", "c9"):
        held = pins.pin(store, feature, review.CAP_DECISION, "ACME-1", [], stage="branch", head=head)
        assert (held.subject, held.version) == (latest.subject, review.cap_version(latest))
    assert "3 rounds left a P1 open, 1 P1 open at c3" in held.detail


def test_cap_pin_refuses_off_the_cap_and_after_proceed():
    store, _ = asked()
    capped(store, P2)
    feature = features.open_feature(store, "ACME")
    with pytest.raises(Refusal, match="has not reached its cap"):
        pins.pin(store, feature, review.CAP_DECISION, "ACME-1", [], stage="branch")
    store, _ = asked()
    latest = capped(store, P1)
    proceed = Record(
        review.CAP_DECISION, latest.subject, review.cap_version(latest), "person:pat", {"answer": "Proceed"}
    )
    store.put_record("s1", proceed)
    feature = features.open_feature(store, "ACME")
    with pytest.raises(Refusal, match="has not reached its cap"):
        pins.pin(store, feature, review.CAP_DECISION, "ACME-1", [], stage="branch")


def test_finding_pin_shows_what_the_finding_touches_and_the_proposal():
    store, _ = asked()
    delivery = P2 | {"changes_delivery": True, "touches": "Contract: ships the same day"}
    found = review.parse_findings(json.dumps([delivery]))
    round = review.round_record("s1", "branch", review.RoundPlan(1, False, ""), "hash1", found, "c1")
    store.put_record("s1", round)
    [finding] = round.data["findings"]
    store.put_record("s1", asks.proposal_record(finding["id"], round.version, "the spec allows it", review.ACCEPT))
    feature = features.open_feature(store, "ACME")
    held = pins.pin(store, feature, review.DECISION, "ACME-1", [], finding=finding["id"])
    assert held.detail == (
        "P2 correctness a.py:1 A hole.; touches: Contract: ships the same day;"
        " the hand proposes to accept it: the spec allows it"
    )


def test_feature_cap_pin_holds_every_live_story_and_takes_only_the_project():
    store, _ = asked()
    feature = features.open_feature(store, "ACME")
    project = feature.project["id"]
    found = review.parse_findings(json.dumps([P1]))
    for n in (1, 2, 3):
        plan = review.RoundPlan(n, scoped=n > 1, base_hash="")
        for story in feature.live:
            store.put_record(story["id"], review.round_record(project, "feature", plan, f"hash{n}", found))
    feature = features.open_feature(store, "ACME")
    held = pins.pin(store, feature, review.CAP_DECISION, project, [], stage="feature")
    latest = review.last(review.rounds_of(store.records("s1"), f"{project}:feature"))
    assert (held.subject, held.version) == (latest.subject, review.cap_version(latest))
    assert held.stories == [s["id"] for s in feature.live] and held.label == "Alpha"
    with pytest.raises(Refusal, match="not the open feature"):
        pins.pin(store, feature, review.CAP_DECISION, "ACME-1", [], stage="feature")


def test_finding_pin_takes_the_project_for_a_feature_finding():
    store, _ = asked()
    feature = features.open_feature(store, "ACME")
    project = feature.project["id"]
    found = review.parse_findings(json.dumps([P2]))
    round = review.round_record(project, "feature", review.RoundPlan(1, False, ""), "hash1", found)
    for story in feature.live:
        store.put_record(story["id"], round)
    [finding] = round.data["findings"]
    feature = features.open_feature(store, "ACME")
    held = pins.pin(store, feature, review.DECISION, project, [], finding=finding["id"])
    assert (held.subject, held.stories, held.label) == (finding["id"], [s["id"] for s in feature.live], "Alpha")
