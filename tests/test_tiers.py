from __future__ import annotations

import pytest

from deckhand.step import Refusal
from deckhand.tiers import NONE, OPTIONS, TIERS, chosen, from_record


def test_options_ask_from_the_highest_tier_down_and_end_with_none():
    assert OPTIONS == [
        "Jobs, retries, webhooks, queues or two writers",
        "A flow or an entity with several states",
        "A data rule: ownership, permissions, uniqueness or money",
        "None of these",
    ]
    assert [t.level for t in TIERS] == [3, 2, 1, 0]
    assert OPTIONS[-1] == NONE


@pytest.mark.parametrize("answer", ["Jobs, retries, webhooks, queues or two writers", NONE])
def test_chosen_reads_one_option_even_when_its_label_holds_commas(answer: str):
    assert chosen(answer).label == answer


@pytest.mark.parametrize(
    "answer", [", ".join(OPTIONS[:2]), "A flow or an entity with several states, free text", "anything"]
)
def test_chosen_refuses_anything_but_one_option(answer: str):
    with pytest.raises(Refusal, match="not one of the offered options"):
        chosen(answer)


def test_from_record_reads_the_stored_level_and_fact():
    for tier in TIERS:
        assert from_record({"level": tier.level, "fact": tier.fact}) == tier


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"facts": ["concurrency"]},
        {"level": 2, "fact": "concurrency"},
        {"level": 4, "fact": "pure_algorithm"},
        {"level": 1, "fact": ["data_rule"]},
    ],
)
def test_from_record_refuses_a_missing_or_inconsistent_record(data: dict):
    assert from_record(data) is None
