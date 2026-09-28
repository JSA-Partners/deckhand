"""The fleet: every story the project holds, what its log allows, and what disagrees."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from deckhand import fleet, sessions

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "acme/widgets"


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


def test_a_row_carries_the_labels_the_issue_holds():
    """The board cannot say which of its issues are the process's business, so the read carries the labels."""
    found = {story.number: story for story in fleet.stories(_nodes())}
    assert found[253].issue.labels == ("deckhand",)
    assert found[120].issue.labels == ()


def test_a_story_without_points_says_so():
    assert {story.number: story for story in fleet.stories(_nodes())}[268].points is None


def test_a_row_knows_its_key():
    assert {story.number: story for story in fleet.stories(_nodes())}[13].key == ("acme/gadgets", 13)


def _story(number: int):
    return {story.number: story for story in fleet.stories(_nodes())}[number]


def test_a_started_story_can_only_be_in_progress():
    assert fleet.allowed(_story(120)) == ("In Progress",)


def test_a_story_with_a_pull_request_is_in_review():
    """finish writes In Review, and nothing moves a story back out of it."""
    assert fleet.allowed(_story(117)) == ("In Review",)


def test_a_reviewed_story_may_still_be_waiting_to_be_boarded():
    """Review leaves a story in Ready, so that is what a repair writes: boarding needs a kind and points."""
    assert fleet.allowed(_story(253)) == ("Ready", "Backlog", "Draft")


def test_a_story_with_only_a_draft_entry_is_a_draft():
    """An amend moves a drafting story to Refinement, so that is a place to be and not an anomaly."""
    assert fleet.allowed(_story(268)) == ("Draft", "Refinement")


def test_a_backlog_story_with_no_blocker_is_ready():
    assert fleet.note(_story(253), [], behind=False) == "ready"


def test_a_backlog_story_says_what_it_waits_on():
    blockers = [("acme/widgets", 253, "Seed the role matrix")]
    assert fleet.note(_story(257), blockers, behind=False) == "waits on #253"


def test_a_blocker_in_another_repository_is_named_in_full():
    blockers = [("acme/gadgets", 13, "Deploy")]
    assert fleet.note(_story(257), blockers, behind=False) == "waits on acme/gadgets#13"


def test_an_in_progress_story_that_waits_says_so_whatever_its_column():
    """Built, rebased and reviewed still means waiting when the blocker has not merged."""
    blockers = [("acme/widgets", 253, "Seed the role matrix")]
    assert fleet.note(_story(120), blockers, behind=False) == "waits on #253"


def test_a_pull_request_behind_main_is_the_note():
    assert fleet.note(_story(117), [], behind=True) == "pull request behind main"


def test_a_story_in_flight_with_a_pull_request_says_so():
    assert fleet.note(_story(117), [], behind=False) == "pull request open"


def test_a_story_marked_done_that_never_shipped_says_what_it_really_is():
    assert fleet.note(_story(268), [], behind=False) == "review not run"


def test_a_story_in_verification_says_how_many_items_are_left():
    """Verification is where a merged story waits on its own after-the-merge boxes, not a column gone wrong."""
    one = fleet.stories(_closed("Verification", OWED))[0]
    two = fleet.stories(_closed("Verification", OWED + "- [ ] Tell the client\n"))[0]

    assert fleet.note(one, [], behind=False) == "merged, 1 item left"
    assert fleet.note(two, [], behind=False) == "merged, 2 items left"


BLOCKERS = {
    ("acme/widgets", 257): [("acme/widgets", 253, "Seed the role matrix")],
    ("acme/gadgets", 258): [("acme/widgets", 257, "Export the role matrix")],
    ("acme/widgets", 253): [],
    ("acme/gadgets", 13): [],
}


def _pulse(story: str, label: str = "a", repo: str = "acme/widgets", idle: float = 0.0):
    return sessions.Pulse(
        label=label,
        session=label,
        repo=repo,
        story=story,
        command="next",
        idle=idle,
        tokens=1000,
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
    entry = next(item for item in found if item.number == 120)
    assert entry.what == "In Progress, no session open"
    assert entry.fix == "none"


def test_a_story_held_by_a_blocker_is_not_reported_as_abandoned():
    """A branch that must not merge yet is meant to have no session on it; its row already says so."""
    blockers = {**BLOCKERS, ("acme/widgets", 120): [("acme/widgets", 253, "Seed the role matrix")]}
    found = fleet.anomalies(fleet.stories(_nodes()), blockers, behind=set(), pulses=[])
    assert [item for item in found if item.number == 120 and "no session open" in item.what] == []


def test_a_story_two_sessions_share_is_reported():
    pulses = [_pulse("117", "a"), _pulse("117", "b")]
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    entry = next(item for item in found if item.number == 117)
    assert entry.what == "sessions a and b are both on it"


def test_a_long_idle_session_is_not_counted_as_a_duplicate():
    """A closed window keeps its row until it ages out; it must not ask for a second closing."""
    pulses = [_pulse("117", "a", idle=60.0), _pulse("117", "b", idle=3 * 3600.0)]
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    assert not [item for item in found if item.number == 117 and "both on it" in item.what]


def test_two_recently_active_sessions_are_still_a_duplicate():
    """The rule earns its place only while it still fires for the case it was written for."""
    pulses = [_pulse("117", "a", idle=60.0), _pulse("117", "b", idle=120.0)]
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    assert [item for item in found if item.number == 117 and "both on it" in item.what]


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
    found = fleet.anomalies(kept, BLOCKERS, behind=set(), pulses=[_pulse("117"), _pulse("120")])
    assert [item.number for item in found] == []


def test_a_finished_story_is_no_anomaly_and_needs_no_repair():
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=[_pulse("120")])
    assert [item.number for item in found if item.number == 117] == []


def test_a_read_gathers_the_stories_the_blockers_and_the_behind_set(fake_gh, settings, monkeypatch):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-items.json"))
    monkeypatch.setenv("GH_PR_STATE", "OPEN")
    monkeypatch.setenv("GH_PR_MERGE_STATE", "BEHIND")
    read = fleet.read(settings)
    assert [story.number for story in read.stories] == [253, 257, 258, 13, 117, 268, 120]
    assert read.blockers[("acme/widgets", 257)] == [("acme/widgets", 253, "Seed")]
    assert read.behind == {("acme/widgets", 117)}
    assert read.missing == []
    assert read.me == "mjm"


def test_a_read_asks_for_no_blockers_one_story_at_a_time(fake_gh, gh_calls, settings, monkeypatch):
    """Blockers come with the board query, so a draft's edges are read too and nothing else is."""
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-items.json"))
    fleet.read(settings)
    assert not [call for call in gh_calls() if "/dependencies/blocked_by" in call]


def test_a_row_carries_its_assignees():
    assert _story(117).assignees == ("mjm",)
    assert _story(253).assignees == ()


def test_a_teammates_story_in_flight_is_not_the_readers_anomaly():
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=[], me="ariav")
    assert not [item for item in found if item.number == 120 and "no session open" in item.what]


def test_the_readers_own_story_in_flight_is_still_reported():
    found = fleet.anomalies(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=[], me="mjm")
    assert [item for item in found if item.number == 120 and "no session open" in item.what]


def test_an_open_issue_with_a_log_and_no_board_item_is_missing(fake_gh, settings, tmp_path, monkeypatch):
    listing = tmp_path / "list.json"
    listing.write_text(json.dumps([{"number": 281}]))
    drafted = tmp_path / "281.json"
    drafted.write_text(
        json.dumps(
            {
                "number": 281,
                "title": "Never boarded",
                "state": "OPEN",
                "url": "https://github.com/acme/widgets/issues/281",
                "body": "### Story",
                "comments": [
                    {"body": "Drafted: from a request", "createdAt": "2026-09-15T00:00:00Z", "author": {"login": "mjm"}}
                ],
            }
        )
    )
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-items.json"))
    monkeypatch.setenv("GH_ISSUE_LIST_ACME_WIDGETS", str(listing))
    monkeypatch.setenv("GH_ISSUE_FILE_281", str(drafted))
    assert fleet.read(settings).missing == [("acme/widgets", 281, "https://github.com/acme/widgets/issues/281")]


def _foreign(status: str) -> list[dict]:
    """A board item for an issue the process never wrote to: no log, and a column of its own."""
    return [
        {
            "id": "I_96",
            "content": {
                "number": 96,
                "title": "Client-side CSV export",
                "url": "https://github.com/acme/widgets/issues/96",
                "state": "OPEN",
                "closedAt": None,
                "repository": {"nameWithOwner": REPO},
                "comments": {"nodes": []},
            },
            "fieldValues": {"nodes": [{"name": status, "field": {"name": "Status"}}]},
        }
    ]


def _closed(status: str, plan: str = "") -> list[dict]:
    """A board item for a story whose pull request merged, with `plan` as its Plan."""
    return [
        {
            "id": "I_301",
            "content": {
                "number": 301,
                "title": "Filter the grant lookup",
                "url": "https://github.com/acme/widgets/issues/301",
                "state": "CLOSED",
                "closedAt": "2026-09-20T09:00:00Z",
                "body": f"### Plan\n\n{plan}",
                "repository": {"nameWithOwner": REPO},
                "comments": {
                    "nodes": [
                        {
                            "body": "Drafted: from a request",
                            "createdAt": "2026-09-15T00:00:00Z",
                            "author": {"login": "mjm"},
                        }
                    ]
                },
            },
            "fieldValues": {"nodes": [{"name": status, "field": {"name": "Status"}}]},
        }
    ]


OWED = "### After the merge\n\n- [ ] Deploy the migration\n"


def test_a_closed_story_left_in_pending_review_is_an_anomaly():
    """Nothing runs when GitHub closes the issue on merge, so a story nobody revisits is caught here."""
    found = fleet.anomalies(fleet.stories(_closed("In Review")), {}, set(), [])

    assert [(item.number, item.fix) for item in found] == [(301, "Status Done")]


def test_a_closed_story_with_items_left_wants_verification():
    """The column a repair writes follows the boxes, not the fact that it is closed."""
    found = fleet.anomalies(fleet.stories(_closed("In Review", OWED)), {}, set(), [])

    assert [(item.number, item.fix) for item in found] == [(301, "Status Verification")]


def test_a_closed_story_in_verification_is_no_anomaly():
    """Items left after the merge is a real place to be, not a fault."""
    assert fleet.anomalies(fleet.stories(_closed("Verification", OWED)), {}, set(), []) == []


def test_an_issue_the_process_never_touched_is_not_judged():
    found = fleet.anomalies(fleet.stories(_foreign("In Review")), {}, set(), [])
    assert found == []


def test_an_issue_the_process_never_touched_says_so_in_its_row():
    story = fleet.stories(_foreign("In Review"))[0]
    assert fleet.note(story, [], behind=False) == "not a deckhand story"


def test_a_loop_through_a_draft_names_every_story_on_it():
    blockers = {
        ("acme/widgets", 257): [("acme/widgets", 268, "Domains")],
        ("acme/widgets", 268): [("acme/widgets", 253, "Seed")],
        ("acme/widgets", 253): [("acme/widgets", 257, "Authorize")],
    }
    found = fleet.anomalies(fleet.stories(_nodes()), blockers, behind=set(), pulses=[])
    entry = next(item for item in found if item.number == 257 and "circle" in item.what)
    assert entry.what == "blockers run in a circle: #257 -> #268 -> #253 -> #257"
    assert entry.fix == "captain apply --unblock on one edge"


def test_an_archived_item_is_set_aside_rather_than_listed():
    """An archived story is read in so it can be reported, and kept out of the board's own tables."""
    nodes = _nodes()
    for node in nodes:
        if (node.get("content") or {}).get("number") == 117:
            node["isArchived"] = True
    found = {story.number: story for story in fleet.stories(nodes)}
    assert found[117].archived is True
    assert found[253].archived is False


def test_an_archived_story_the_process_owns_is_an_anomaly():
    """Archiving hides a story from every board read, so the one deckhand still owns has to be named."""
    archived = [dataclasses.replace(_story(120), archived=True)]
    found = fleet.anomalies([], {}, set(), [], archived=archived)
    assert [(a.number, a.what, a.fix) for a in found] == [
        (120, "archived, and still In Progress", "unarchive it on the board")
    ]


def test_an_archived_story_that_is_done_is_not_an_anomaly():
    """Archiving finished work is what the project's own workflow is for; 90 items arrived that way."""
    archived = [dataclasses.replace(fleet.stories(_closed("Done"))[0], archived=True)]
    assert fleet.anomalies([], {}, set(), [], archived=archived) == []


def test_an_archived_issue_the_process_never_wrote_to_is_not_an_anomaly():
    """A board carries issues that are not deckhand's, and archiving one of those is nobody's business."""
    archived = [dataclasses.replace(fleet.stories(_foreign("Backlog"))[0], archived=True)]
    assert fleet.anomalies([], {}, set(), [], archived=archived) == []
