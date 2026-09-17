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


def _story(number: int):
    return {story.number: story for story in fleet.stories(_nodes())}[number]


def test_a_started_story_can_only_be_in_progress():
    assert fleet.allowed(_story(117)) == ("In Progress",)


def test_a_reviewed_story_may_still_be_waiting_to_be_boarded():
    assert fleet.allowed(_story(253)) == ("Backlog", "Draft")


def test_a_story_with_only_a_draft_entry_is_a_draft():
    assert fleet.allowed(_story(268)) == ("Draft",)


def test_a_backlog_story_with_no_blocker_is_ready():
    assert fleet.note(_story(253), [], behind=False) == "ready"


def test_a_backlog_story_says_what_it_waits_on():
    blockers = [("acme/widgets", 253, "Seed the role matrix")]
    assert fleet.note(_story(257), blockers, behind=False) == "waits on #253"


def test_a_blocker_in_another_repository_is_named_in_full():
    blockers = [("acme/gadgets", 13, "Deploy")]
    assert fleet.note(_story(257), blockers, behind=False) == "waits on acme/gadgets#13"


def test_a_pull_request_behind_main_is_the_note():
    assert fleet.note(_story(117), [], behind=True) == "pull request behind main"


def test_a_story_in_flight_with_a_pull_request_says_so():
    assert fleet.note(_story(117), [], behind=False) == "pull request open"


def test_a_story_marked_done_that_never_shipped_says_what_it_really_is():
    assert fleet.note(_story(268), [], behind=False) == "review not run"
