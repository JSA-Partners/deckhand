"""The fleet: every story the project holds, what its log allows, and what disagrees."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from deckhand import board, fleet, issue, sessions

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
    assert fleet.stories(_foreign("Backlog"))[0].issue.labels == ()


def test_a_story_without_points_says_so():
    assert {story.number: story for story in fleet.stories(_nodes())}[268].points is None


def test_a_row_knows_its_key():
    assert {story.number: story for story in fleet.stories(_nodes())}[13].key == ("acme/gadgets", 13)


def _story(number: int):
    return {story.number: story for story in fleet.stories(_nodes())}[number]


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
    """Two typed sources, so they can disagree: the column says finished and the issue is still open."""
    assert fleet.note(_story(268), [], behind=False) == "Done, but the issue is open"


def test_a_story_marked_done_whose_issue_is_open_is_an_anomaly():
    found = fleet.anomalies(fleet.stories(_nodes()), {}, set(), [])

    assert [(a.number, a.what) for a in found if a.number == 268] == [(268, "Done, but the issue is open")]


def test_a_story_in_verification_says_how_many_items_are_left():
    """Verification is where a merged story waits on its own after-the-merge boxes, not a column gone wrong."""
    one = fleet.stories(_closed("Verification", OWED))[0]
    two = fleet.stories(_closed("Verification", OWED + "- [ ] Tell the client\n"))[0]

    assert fleet.note(one, [], behind=False) == "merged, 1 item left"
    assert fleet.note(two, [], behind=False) == "merged, 2 items left"


def _in(status: str, body: str):
    story = _story(253)
    return dataclasses.replace(story, status=status, issue=dataclasses.replace(story.issue, body=body))


def test_a_parked_feature_is_a_draft_to_settle():
    assert fleet.note(_in("Draft", "## Requirements\n\nA feature.\n"), [], behind=False) == "draft to settle"


def test_a_draft_with_stories_named_is_a_draft_to_write():
    body = "## Requirements\n\nA feature.\n\n## Stories\n\n1. #57 Grant endpoint | Persist grants.\n"
    assert fleet.note(_in("Draft", body), [], behind=False) == "draft to write"


def test_a_written_story_below_ready_is_review_not_run():
    body = "### Story\n\nAs a user, I want x, so that y.\n"
    assert fleet.note(_in("Refinement", body), [], behind=False) == "review not run"


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
    monkeypatch.setenv("GH_PR_ISSUES", "117")  # 120 is building and has none
    read = fleet.read(settings)
    assert [story.number for story in read.stories] == [253, 257, 258, 13, 117, 268, 120]
    assert read.blockers[("acme/widgets", 257)] == [("acme/widgets", 253, "Seed")]
    assert read.behind == {("acme/widgets", 117)}
    assert read.missing == []
    assert read.me == "mjm"


def test_a_read_keeps_every_open_pull_request_it_read(fake_gh, settings, monkeypatch):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-items.json"))
    monkeypatch.setenv("GH_PR_STATE", "OPEN")
    monkeypatch.setenv("GH_PR_ISSUES", "117")
    monkeypatch.setenv("GH_PR_FILES", json.dumps(["api/auth.go"]))
    read = fleet.read(settings)
    assert list(read.pulls) == [("acme/widgets", 117)]
    assert read.pulls[("acme/widgets", 117)].files == ("api/auth.go",)
    assert read.behind == set()


def test_a_read_keeps_no_pull_request_that_is_no_longer_open(fake_gh, settings, monkeypatch):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-items.json"))
    monkeypatch.setenv("GH_PR_STATE", "CLOSED")
    monkeypatch.setenv("GH_PR_MERGE_STATE", "DIRTY")
    read = fleet.read(settings)
    assert read.pulls == {}
    assert read.behind == set()


def _pull(number: int, behind: bool | None) -> issue.PullRequest:
    url = f"https://github.com/acme/widgets/pull/{number}"
    return issue.PullRequest(number, url, "OPEN", False, behind, (), 0)


def test_behind_is_every_pull_request_main_moved_past_and_no_other():
    pulls = {(REPO, 1): _pull(11, True), (REPO, 2): _pull(12, False), (REPO, 3): _pull(13, None)}
    assert fleet.Fleet(stories=[], blockers={}, missing=[], pulls=pulls).behind == {(REPO, 1)}


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
                "labels": [{"name": "deckhand"}],
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


def test_an_open_issue_without_the_label_is_not_missing(fake_gh, settings, tmp_path, monkeypatch):
    """A repository holds issues that are not the process's business, and none of them wants boarding."""
    listing = tmp_path / "list.json"
    listing.write_text(json.dumps([{"number": 281}]))
    theirs = tmp_path / "281.json"
    theirs.write_text(
        json.dumps(
            {
                "number": 281,
                "title": "Somebody else's",
                "state": "OPEN",
                "url": "https://github.com/acme/widgets/issues/281",
                "body": "### Story",
                "labels": [],
                "comments": [],
            }
        )
    )
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-items.json"))
    monkeypatch.setenv("GH_ISSUE_LIST_ACME_WIDGETS", str(listing))
    monkeypatch.setenv("GH_ISSUE_FILE_281", str(theirs))

    assert fleet.read(settings).missing == []


def test_a_closed_story_left_in_an_earlier_column_is_an_anomaly():
    """Only a closed story reaches the last two columns, so closed anywhere else is two facts disagreeing."""
    found = fleet.anomalies(fleet.stories(_closed("In Review")), {}, set(), [])

    assert [(a.number, a.what, a.fix) for a in found] == [(301, "closed, but In Review", "run next 301")]


def test_a_story_closed_as_not_planned_is_sent_to_withdraw():
    nodes = _closed("Draft")
    nodes[0]["content"]["stateReason"] = "NOT_PLANNED"

    found = fleet.anomalies(fleet.stories(nodes), {}, set(), [])

    assert [(a.what, a.fix) for a in found] == [
        ("closed, but Draft", 'captain apply --withdraw acme/widgets#301 --note "<why>"')
    ]


def test_a_closed_story_in_verification_is_not_an_anomaly():
    assert fleet.anomalies(fleet.stories(_closed("Verification")), {}, set(), []) == []


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
                "labels": {"nodes": [{"name": "deckhand"}]},
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


def test_an_item_carries_whether_it_is_archived():
    """The query asks for both states, so every row has to say which one it came back in."""
    nodes = _nodes()
    for node in nodes:
        if (node.get("content") or {}).get("number") == 117:
            node["isArchived"] = True
    found = {story.number: story for story in fleet.stories(nodes)}
    assert found[117].archived is True
    assert found[253].archived is False


def test_the_items_query_asks_for_archived_items_and_says_which_they_are():
    """Without the argument the project returns only unarchived items, and an archived story is lost."""
    assert "archivedStates:[ARCHIVED,NOT_ARCHIVED]" in fleet.ITEMS_QUERY
    assert "isArchived" in fleet.ITEMS_QUERY


def _archived(tmp_path: Path, number: int) -> str:
    """The board fixture with `number` archived, as a path for GH_PROJECT_ITEMS_FILE."""
    data = json.loads((FIXTURES / "captain-items.json").read_text())
    for node in data["data"]["organization"]["projectV2"]["items"]["nodes"]:
        if (node.get("content") or {}).get("number") == number:
            node["isArchived"] = True
    path = tmp_path / "archived-items.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_a_read_sets_an_archived_story_aside(fake_gh, settings, monkeypatch, tmp_path):
    """An archived story leaves the tables that drive the board and stays reachable to be reported."""
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", _archived(tmp_path, 117))

    read = fleet.read(settings)

    assert 117 not in [story.number for story in read.stories]
    assert [story.number for story in read.archived] == [117]
    assert ("acme/widgets", 117) not in read.blockers


def test_a_read_does_not_call_an_archived_story_missing(fake_gh, settings, monkeypatch, tmp_path):
    """An archived story is on the board, so reporting it as never boarded would be a second wrong answer.

    The repository lists it as open and its issue carries a `Drafted:` entry, which is everything the
    missing check looks for; only its key being on the board keeps it off the list.
    """
    listed = tmp_path / "open-issues.json"
    listed.write_text(json.dumps([{"number": 117}]))
    drafted = tmp_path / "issue-117.json"
    drafted.write_text(
        json.dumps(
            {
                "number": 117,
                "title": "Warn before the export runs",
                "state": "OPEN",
                "url": "https://github.com/acme/widgets/issues/117",
                "body": "### Story\n\nAs a user, I want a warning, so that nothing is lost.",
                "comments": [
                    {"author": {"login": "mjm"}, "createdAt": "2026-09-20T09:00:00Z", "body": "Drafted: from a request"}
                ],
            }
        )
    )
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", _archived(tmp_path, 117))
    monkeypatch.setenv("GH_ISSUE_LIST_ACME_WIDGETS", str(listed))
    monkeypatch.setenv("GH_ISSUE_FILE_117", str(drafted))

    read = fleet.read(settings)

    assert read.missing == []


def test_an_archived_story_the_process_owns_is_an_anomaly():
    """Archiving hides a story from every board read, so the one deckhand still owns has to be named."""
    archived = [dataclasses.replace(_story(120), archived=True)]
    found = fleet.anomalies([], {}, set(), [], archived=archived)
    assert [(a.number, a.what, a.fix) for a in found] == [
        (120, "archived, and still In Progress", "unarchive it on the board")
    ]


def test_an_archived_story_that_is_done_is_not_an_anomaly():
    """Archiving finished work is what the project's own workflow is for."""
    archived = [dataclasses.replace(fleet.stories(_closed("Done"))[0], archived=True)]
    assert fleet.anomalies([], {}, set(), [], archived=archived) == []


def test_an_archived_issue_the_process_never_wrote_to_is_not_an_anomaly():
    """A board carries issues that are not deckhand's, and archiving one of those is nobody's business."""
    archived = [dataclasses.replace(fleet.stories(_foreign("Backlog"))[0], archived=True)]
    assert fleet.anomalies([], {}, set(), [], archived=archived) == []


# features


def _item(number: int, labels: tuple[str, ...], feature: str | None = None, repo: str = REPO) -> dict:
    content = {
        "number": number,
        "title": f"T{number}",
        "state": "OPEN",
        "closedAt": None,
        "repository": {"nameWithOwner": repo},
        "labels": {"nodes": [{"name": name} for name in labels]},
    }
    values = [] if feature is None else [{"name": f"Name {feature}", "optionId": feature, "field": {"name": "Feature"}}]
    return {"id": f"I_{number}", "content": content, "fieldValues": {"nodes": values}}


def test_the_items_query_asks_for_the_option_id_of_a_single_select():
    assert "ProjectV2ItemFieldSingleSelectValue{ name optionId " in fleet.ITEMS_QUERY
    assert "parent{" not in fleet.ITEMS_QUERY


def test_the_items_query_reads_as_many_field_values_as_the_board_read():
    def _first(query: str) -> str:
        return query.split("fieldValues(first:", 1)[1].split(")", 1)[0]

    assert _first(fleet.ITEMS_QUERY) == _first(board.ITEMS_QUERY)


def _nodes_asked(query: str) -> int:
    """GitHub's node count for a query: each connection's first or last times every connection around it."""
    total, around, depth, opened = 0, [1], 0, []
    for match in re.finditer(r"(?:first|last):(\d+)|[{}]", query):
        if match.group(1):
            total += around[-1] * int(match.group(1))
            opened.append((depth + 1, around[-1] * int(match.group(1))))
        elif match.group() == "{":
            depth += 1
            if opened and opened[-1][0] == depth:
                around.append(opened.pop()[1])
            else:
                around.append(around[-1])
        else:
            depth -= 1
            around.pop()
    return total


def test_the_items_query_reads_a_long_log_within_the_node_limit():
    assert "comments(last:100)" in fleet.ITEMS_QUERY
    assert _nodes_asked(fleet.ITEMS_QUERY) == 18_100
    assert _nodes_asked(fleet.ITEMS_QUERY) < 500_000


def test_a_row_carries_the_feature_option_its_item_holds():
    found = fleet.stories([_item(1, ("deckhand",), feature="OPT_A"), _item(2, ("deckhand",))])

    assert (found[0].feature, found[0].feature_name) == ("OPT_A", "Name OPT_A")
    assert (found[1].feature, found[1].feature_name) == (None, "")


def test_a_story_closed_as_not_planned_reads_as_dropped():
    dropped = _item(1, ("deckhand",))
    dropped["content"].update({"state": "CLOSED", "closedAt": "2026-09-02T00:00:00Z", "stateReason": "NOT_PLANNED"})
    finished = _item(2, ("deckhand",))
    finished["content"].update({"state": "CLOSED", "closedAt": "2026-09-02T00:00:00Z"})

    found = fleet.stories([dropped, finished])

    assert [story.dropped for story in found] == [True, False]


def test_a_story_closed_as_a_duplicate_reads_as_dropped():
    duplicate = _item(1, ("deckhand",))
    duplicate["content"].update({"state": "CLOSED", "closedAt": "2026-09-02T00:00:00Z", "stateReason": "DUPLICATE"})

    assert fleet.stories([duplicate])[0].dropped is True


def test_a_row_carries_when_the_issue_closed():
    finished = _item(1, ("deckhand",))
    finished["content"].update({"state": "CLOSED", "closedAt": "2026-09-02T00:00:00Z"})

    found = fleet.stories([finished, _item(2, ("deckhand",))])

    assert [story.closed_at for story in found] == ["2026-09-02T00:00:00Z", ""]


def test_members_are_the_owned_stories_of_one_feature_and_nothing_dropped():
    dropped = _item(5, ("deckhand",), feature="OPT_A")
    dropped["content"].update({"state": "CLOSED", "closedAt": "2026-09-02T00:00:00Z", "stateReason": "NOT_PLANNED"})
    found = fleet.stories(
        [
            _item(1, ("deckhand",), feature="OPT_A"),
            _item(2, (), feature="OPT_A"),
            _item(3, ("deckhand",), feature="OPT_B"),
            _item(4, ("deckhand",), feature="OPT_A", repo="acme/gadgets"),
            dropped,
        ]
    )

    assert [story.number for story in fleet.members(found, "OPT_A")] == [1, 4]


def _logged(
    number: int,
    *entries: str,
    feature: str | None = None,
    closed: bool = False,
    labels: tuple[str, ...] = ("deckhand",),
    repo: str = REPO,
) -> fleet.Story:
    comments = [issue.Comment(author="claude", body=body, created_at="2026-09-01T00:00:00Z") for body in entries]
    held = issue.Issue(
        number=number,
        title=f"T{number}",
        body="",
        url="",
        state="CLOSED" if closed else "OPEN",
        comments=comments,
        labels=labels,
    )
    return fleet.Story(
        number=number,
        repo=repo,
        title=f"T{number}",
        status="Done" if closed else "Backlog",
        points=1,
        closed=closed,
        item=f"I_{number}",
        issue=held,
        feature=feature,
        feature_name={"OPT_A": "Alpha", "OPT_B": "Beta"}.get(feature or "", ""),
    )


def test_shed_names_every_story_a_split_entry_names():
    story = _logged(1, "Split: into #2, acme/gadgets#3", "Split: #4 Follow on, blocked by this story.")

    assert fleet.shed(story) == [(REPO, 2), ("acme/gadgets", 3), (REPO, 4)]


def test_shed_ignores_an_issue_a_split_title_mentions():
    story = _logged(1, "Split: #45 Fix the crash from #12, blocked by this story.")

    assert fleet.shed(story) == [(REPO, 45)]


def test_a_story_that_never_split_shed_nothing():
    assert fleet.shed(_logged(1, "Started: on the branch")) == []


SPLIT = "Split: #2 Follow on, blocked by this story."


def test_a_story_shed_by_a_feature_s_story_and_left_out_is_a_stray():
    shedder, kid = _logged(1, SPLIT, feature="OPT_A"), _logged(2)

    assert fleet.strays([shedder, kid]) == [(kid, shedder)]


def test_a_story_shed_by_a_story_in_no_feature_is_not_a_stray():
    assert fleet.strays([_logged(1, SPLIT), _logged(2)]) == []


def test_a_shed_story_already_in_a_feature_is_not_a_stray():
    assert fleet.strays([_logged(1, SPLIT, feature="OPT_A"), _logged(2, feature="OPT_B")]) == []


def test_a_finished_shed_story_is_not_a_stray():
    assert fleet.strays([_logged(1, SPLIT, feature="OPT_A"), _logged(2, closed=True)]) == []


def test_a_shed_issue_deckhand_does_not_own_is_not_a_stray():
    assert fleet.strays([_logged(1, SPLIT, feature="OPT_A"), _logged(2, labels=())]) == []


def test_a_stray_is_an_anomaly_that_names_its_fix():
    found = fleet.anomalies([_logged(1, SPLIT, feature="OPT_A"), _logged(2)], {}, set(), [])

    assert [(item.number, item.what, item.fix) for item in found] == [
        (2, "split from a story of Alpha, but in no feature", "epic add OPT_A acme/widgets#2")
    ]


def test_a_stray_fix_names_the_option_id_whatever_the_feature_is_called():
    shedder = dataclasses.replace(_logged(1, SPLIT, feature="OPT_A"), feature_name='Say "hi" | $HOME `id`')

    (found,) = fleet.anomalies([shedder, _logged(2)], {}, set(), [])

    assert found.what == 'split from a story of Say "hi" \\| $HOME `id`, but in no feature'
    assert found.fix == "epic add OPT_A acme/widgets#2"


def test_a_stray_in_another_repository_names_its_ref_in_full():
    gadgets = _logged(4, "Split: #5 Follow on.", feature="OPT_A", repo="acme/gadgets")

    found = fleet.anomalies([gadgets, _logged(5, repo="acme/gadgets")], {}, set(), [])

    assert [item.fix for item in found] == ["epic add OPT_A acme/gadgets#5"]


def test_a_stray_named_in_another_case_is_still_found():
    gadgets = _logged(4, "Split: into Acme/Gadgets#5", feature="OPT_A", repo="acme/gadgets")

    found = fleet.anomalies([gadgets, _logged(5, repo="acme/gadgets")], {}, set(), [])

    assert [item.fix for item in found] == ["epic add OPT_A acme/gadgets#5"]


def test_an_archived_story_that_shed_still_reports_its_open_kid():
    shedder = dataclasses.replace(_logged(1, SPLIT, feature="OPT_A", closed=True), archived=True)
    kid = _logged(2)

    assert fleet.strays([kid], [shedder]) == [(kid, shedder)]
    assert [item.number for item in fleet.anomalies([kid], {}, set(), [], archived=[shedder])] == [2]


def test_an_archived_kid_is_not_a_stray():
    kid = dataclasses.replace(_logged(2), archived=True)

    assert fleet.strays([_logged(1, SPLIT, feature="OPT_A")], [kid]) == []


def test_a_kid_shed_by_two_stories_of_a_feature_is_one_stray():
    first = _logged(1, "Split: #3 Follow on, blocked by this story.", feature="OPT_A")
    second = _logged(2, "Split: #3 Follow on, blocked by this story.", feature="OPT_A")
    kid = _logged(3)

    assert fleet.strays([first, second, kid]) == [(kid, first)]
