"""The build order: what can start first, by the work it frees, and what waits on what."""

from __future__ import annotations

import json
from pathlib import Path

from deckhand import fleet, order

FIXTURES = Path(__file__).parent / "fixtures"

BLOCKERS = {
    ("acme/widgets", 257): [("acme/widgets", 253, "Seed the role matrix")],
    ("acme/gadgets", 258): [("acme/widgets", 257, "Export the role matrix")],
    ("acme/widgets", 253): [],
    ("acme/gadgets", 13): [],
}


def _nodes() -> list[dict]:
    data = json.loads((FIXTURES / "captain-items.json").read_text())
    return data["data"]["organization"]["projectV2"]["items"]["nodes"]


def _backlog():
    return [story for story in fleet.stories(_nodes()) if story.status == "Backlog"]


def test_the_story_that_unlocks_the_most_goes_first():
    assert [row.story.number for row in order.ranked(_backlog(), BLOCKERS)] == [253, 13, 257, 258]


def test_the_reason_counts_the_work_unlocked():
    reasons = {row.story.number: row.why for row in order.ranked(_backlog(), BLOCKERS)}
    assert reasons[253] == "ready, unblocks 2 stories, 7 pts"
    assert reasons[13] == "ready, unblocks nothing"
    assert reasons[257] == "waits on #253"


def test_a_story_never_precedes_what_it_waits_on():
    placed = [row.story.number for row in order.ranked(_backlog(), BLOCKERS)]
    assert placed.index(257) < placed.index(258)


def test_a_cycle_leaves_everyone_placed():
    blockers = {
        ("acme/widgets", 253): [("acme/widgets", 257, "Export")],
        ("acme/widgets", 257): [("acme/widgets", 253, "Seed")],
        ("acme/gadgets", 13): [],
        ("acme/gadgets", 258): [],
    }
    assert sorted(row.story.number for row in order.ranked(_backlog(), blockers)) == [13, 253, 257, 258]
