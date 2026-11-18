from __future__ import annotations

import pytest

from deckhand.step import Refusal
from deckhand.tiers import Tier, tier


def test_no_facts_is_tier_0():
    assert tier(set()) == Tier(level=0, contract=False, tools_key="0")


def test_highest_fact_wins():
    assert tier({"data_rule", "concurrency"}).level == 3


def test_crossing_sets_contract_only():
    assert tier({"crosses_repositories"}) == Tier(level=0, contract=True, tools_key="0")


def test_unknown_fact_refuses():
    with pytest.raises(Refusal) as refused:
        tier({"data_rule", "nope"})

    assert "nope" in refused.value.rule
