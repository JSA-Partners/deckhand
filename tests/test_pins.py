from __future__ import annotations

import pytest

from deckhand import asks, features, pins
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
