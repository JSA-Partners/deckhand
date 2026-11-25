from __future__ import annotations

import pytest

from deckhand.records import Record
from deckhand.tiers import FACTS, TIERS, from_record, named, tools


def test_facts_run_from_the_highest_tier_down():
    assert FACTS == ("concurrency", "multi_step", "data_rule", "none")
    assert [t.level for t in TIERS] == [3, 2, 1, 0]


@pytest.mark.parametrize("fact", ["high", "Concurrency", ["none"], None])
def test_named_reads_only_a_known_fact(fact: object):
    assert named(fact) is None
    assert named("multi_step") == TIERS[1]


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


TOOLS = {1: ["pytest"], 3: ["pytest", "hypothesis"], 4: ["lake build"]}


def test_lean_decision_makes_tier_4():
    lean = [Record("lean", "s1", "v1", "person:s", {"answer": "Prove it in Lean"})]
    assert tools(TOOLS, 1, lean, "s1") == ["pytest", "lake build"]
    assert tools(TOOLS, 3, lean, "s1") == ["pytest", "hypothesis", "lake build"]


def test_tier_tools_without_a_lean_decision_for_this_story_stay_as_they_are():
    other = [Record("lean", "s2", "v1", "person:s", {})]
    assert tools(TOOLS, 1, other, "s1") == ["pytest"]
    assert tools(TOOLS, 0, [], "s1") == []
