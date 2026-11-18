from __future__ import annotations

import pytest

from deckhand.step import Refusal
from deckhand.tiers import FACTS, Tier, chosen, tier


def test_no_facts_is_tier_0():
    assert tier(set()) == Tier(level=0, contract=False, data_rule=False, tools_key="0")


def test_highest_fact_wins():
    assert tier({"data_rule", "concurrency"}).level == 3


def test_crossing_sets_contract_only():
    assert tier({"crosses_repositories"}) == Tier(level=0, contract=True, data_rule=False, tools_key="0")


def test_unknown_fact_refuses():
    with pytest.raises(Refusal) as refused:
        tier({"data_rule", "nope"})

    assert "nope" in refused.value.rule


def test_chosen_reads_a_multi_select_answer_whose_labels_hold_commas():
    answer = ", ".join(f.question for f in FACTS[:2])
    assert chosen(answer) == {FACTS[0].key, FACTS[1].key}
    assert tier(chosen(answer)).level == 2
    with pytest.raises(Refusal, match="not a selection"):
        chosen(FACTS[0].question + ", free text")
