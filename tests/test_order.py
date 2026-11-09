"""The build order: what can start first, by the work it frees, and what waits on what."""

from __future__ import annotations

import json
from dataclasses import replace
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


def test_the_next_line_keeps_the_ranked_order_of_the_repositories():
    """The urgent repository leads, so the line cannot be sorted by the repository's own name."""
    found = list(fleet.stories(_nodes()))
    read = fleet.Fleet(
        stories=found,
        blockers={story.key: list(story.blocked_by) for story in found if story.status != "Done"},
        missing=[],
    )

    assert order.next_line(read, []) == (
        "Next per repository: widgets 253 (no session open), gadgets 258 (no session open)"
    )


def test_the_next_line_names_the_draft_a_blocked_repository_waits_on():
    """A Draft is not in the order table, so the Next line is the only place its chain can be seen."""
    found = {story.number: story for story in fleet.stories(_nodes())}
    draft = replace(found[268], status="Draft")
    read = fleet.Fleet(
        stories=[found[257], draft],
        blockers={found[257].key: [("acme/widgets", 268, "Domains")], draft.key: []},
        missing=[],
    )

    assert order.next_line(read, []) == "Next per repository: widgets: #268 is Draft, unblocking 1 story"


def test_the_backlog_leaves_out_an_issue_deckhand_does_not_own():
    found = list(fleet.stories(_nodes()))
    bug = replace(found[0], issue=replace(found[0].issue, labels=("bug",)))
    foreign = replace(found[3], issue=replace(found[3].issue, labels=()))
    read = fleet.Fleet(stories=[bug, found[1], found[2], foreign], blockers={}, missing=[])

    assert sorted(row.story.number for row in order.backlog(read)) == [257, 258]


def test_the_next_line_names_a_blocker_deckhand_does_not_own_in_backlog():
    """A foreign Backlog issue is not in the order table, so the Next line is the only place it shows."""
    found = {story.number: story for story in fleet.stories(_nodes())}
    foreign = replace(found[268], status="Backlog", issue=replace(found[268].issue, labels=()))
    read = fleet.Fleet(
        stories=[found[257], foreign],
        blockers={found[257].key: [("acme/widgets", 268, "Domains")], foreign.key: []},
        missing=[],
    )

    assert order.next_line(read, []) == "Next per repository: widgets: #268 is Backlog, unblocking 1 story"


def _in(features: dict[int, str]):
    """The backlog with each numbered story moved into the Feature option given for it."""
    return [replace(story, feature=features.get(story.number)) for story in _backlog()]


def _placed(stories, features: tuple[str, ...]) -> list[int]:
    places = order.places(stories, BLOCKERS, features)
    return [row.story.number for row in order.ranked(stories, BLOCKERS, places)]


def test_the_earlier_feature_ranks_first_whatever_the_work_freed():
    stories = _in({13: "OPT_A", 253: "OPT_B", 257: "OPT_B", 258: "OPT_B"})
    assert _placed(stories, ("OPT_A", "OPT_B")) == [13, 253, 257, 258]


def test_a_blocker_ranks_with_the_earliest_feature_waiting_on_it():
    """#253 is in the last feature, but #257 in the first waits on it, so both come before #13."""
    stories = _in({13: "OPT_B", 253: "OPT_C", 257: "OPT_A", 258: "OPT_C"})
    assert _placed(stories, ("OPT_A", "OPT_B", "OPT_C")) == [253, 257, 13, 258]


def test_a_waiting_story_of_an_earlier_feature_ranks_above_a_ready_one_of_a_later():
    """#257 waits on #253, which is building and so not in the backlog; the pipeline still puts it first."""
    stories = [story for story in _in({13: "OPT_B", 257: "OPT_A", 258: "OPT_A"}) if story.number != 253]
    assert _placed(stories, ("OPT_A", "OPT_B")) == [257, 258, 13]


def test_a_story_in_no_feature_ranks_after_every_featured_one():
    """#253 frees the most work, but nothing featured waits on it, so featured #13 starts first."""
    stories = _in({13: "OPT_B"})
    assert _placed(stories, ("OPT_A", "OPT_B")) == [13, 253, 257, 258]


def test_an_unread_pipeline_keeps_the_order_the_work_freed_gives():
    stories = _in({13: "OPT_A", 253: "OPT_B"})
    assert _placed(stories, ()) == [253, 13, 257, 258]
