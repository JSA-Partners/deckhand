"""The fleet: every story the project holds, what its log allows, the build order, and what disagrees."""

from __future__ import annotations

import json
from pathlib import Path

from deckhand import fleet, sessions

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


BLOCKERS = {
    ("acme/widgets", 257): [("acme/widgets", 253, "Seed the role matrix")],
    ("acme/gadgets", 258): [("acme/widgets", 257, "Export the role matrix")],
    ("acme/widgets", 253): [],
    ("acme/gadgets", 13): [],
}


def _backlog():
    return [story for story in fleet.stories(_nodes()) if story.status == "Backlog"]


def test_the_story_that_unlocks_the_most_goes_first():
    ranked = fleet.order(_backlog(), BLOCKERS)
    assert [row.story.number for row in ranked] == [253, 13, 257, 258]


def test_the_reason_counts_the_work_unlocked():
    ranked = {row.story.number: row.why for row in fleet.order(_backlog(), BLOCKERS)}
    assert ranked[253] == "ready, unblocks 2 stories, 7 pts"
    assert ranked[13] == "ready, unblocks nothing"
    assert ranked[257] == "waits on #253"


def test_a_story_never_precedes_what_it_waits_on():
    ranked = [row.story.number for row in fleet.order(_backlog(), BLOCKERS)]
    assert ranked.index(257) < ranked.index(258)


def test_a_cycle_leaves_everyone_placed():
    blockers = {
        ("acme/widgets", 253): [("acme/widgets", 257, "Export")],
        ("acme/widgets", 257): [("acme/widgets", 253, "Seed")],
        ("acme/gadgets", 13): [],
        ("acme/gadgets", 258): [],
    }
    ranked = fleet.order(_backlog(), blockers)
    assert sorted(row.story.number for row in ranked) == [13, 253, 257, 258]


def _pulse(story: str, label: str = "a", repo: str = "acme/widgets"):
    return sessions.Pulse(
        label=label,
        session=label,
        repo=repo,
        story=story,
        command="next",
        idle=60.0,
        cost=1.0,
        waiting=False,
        started=1.0,
        path=Path("x.jsonl"),
    )


def test_a_done_story_whose_issue_is_open_is_an_anomaly():
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=[])
    entry = next(item for item in found if item.number == 268)
    assert entry.what == "Done, but the log allows Draft"
    assert entry.fix == "Status Draft"


def test_a_story_in_flight_with_nobody_on_it_is_reported_and_not_fixed():
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=[])
    entry = next(item for item in found if item.number == 117)
    assert entry.what == "In Progress, no session open"
    assert entry.fix == "none"


def test_a_story_two_sessions_share_is_reported():
    pulses = [_pulse("117", "a"), _pulse("117", "b")]
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    entry = next(item for item in found if item.number == 117)
    assert entry.what == "sessions a and b are both on it"


def test_a_pull_request_behind_main_names_the_command():
    behind = {("acme/widgets", 117)}
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, behind=behind, pulses=[_pulse("117")])
    entry = next(item for item in found if item.number == 117)
    assert entry.fix == "run next 117"


def test_a_drafted_issue_that_never_reached_the_board_is_an_anomaly():
    missing = [("acme/widgets", 281, "https://github.com/acme/widgets/issues/281")]
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, set(), [], missing)
    entry = next(item for item in found if item.number == 281)
    assert (entry.what, entry.fix) == ("drafted, but not on the board", "add it")


def test_a_fleet_that_agrees_with_its_logs_has_no_status_anomaly():
    kept = [story for story in fleet.stories(_nodes()) if story.number != 268]
    found = fleet.anomalies(kept, BLOCKERS, behind=set(), pulses=[_pulse("117")])
    assert [item.number for item in found] == []
