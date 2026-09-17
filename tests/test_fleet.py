"""The fleet: every story the project holds, what its log allows, the build order, and what disagrees."""

from __future__ import annotations

import json
from pathlib import Path

from deckhand import fleet

FIXTURES = Path(__file__).parent / "fixtures"


def _nodes() -> list[dict]:
    data = json.loads((FIXTURES / "captain-items.json").read_text())
    return data["data"]["organization"]["projectV2"]["items"]["nodes"]


def test_a_row_carries_the_board_fields_and_the_issue():
    found = {story.number: story for story in fleet.stories(_nodes())}
    assert found[253].repo == "acme/widgets"
    assert found[253].status == "Backlog"
    assert found[253].points == 3
    assert found[253].item == "I_253"
    assert found[253].closed is False
    assert [comment.body for comment in found[253].issue.comments][0] == "Drafted: from a request"


def test_a_story_without_points_says_so():
    assert {story.number: story for story in fleet.stories(_nodes())}[268].points is None


def test_a_row_knows_its_key():
    assert {story.number: story for story in fleet.stories(_nodes())}[13].key == ("acme/gadgets", 13)
