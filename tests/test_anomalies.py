"""Tests for deckhand.anomalies."""

from __future__ import annotations

import dataclasses

from deckhand import anomalies, fleet
from tests.test_fleet import BLOCKERS, OWED, SPLIT, _closed, _foreign, _logged, _nodes, _pulse, _story


def test_a_story_marked_done_whose_issue_is_open_is_an_anomaly():
    found = anomalies.find(fleet.stories(_nodes()), {}, set(), [])

    assert [(a.number, a.what) for a in found if a.number == 268] == [(268, "Done, but the issue is open")]


def test_a_story_in_flight_with_nobody_on_it_is_reported_and_not_fixed():
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=[])
    entry = next(item for item in found if item.number == 120)
    assert entry.what == "In Progress, no session open"
    assert entry.fix == "none"


def test_a_story_held_by_a_blocker_is_not_reported_as_abandoned():
    """A branch that must not merge yet is meant to have no session on it; its row already says so."""
    blockers = {**BLOCKERS, ("acme/widgets", 120): [("acme/widgets", 253, "Seed the role matrix")]}
    found = anomalies.find(fleet.stories(_nodes()), blockers, behind=set(), pulses=[])
    assert [item for item in found if item.number == 120 and "no session open" in item.what] == []


def test_a_running_session_idle_overnight_still_holds_its_story():
    """The person is away, not gone; the Sessions table already says for how long."""
    pulses = [_pulse("120", "a", idle=17 * 3600.0, live=True)]
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    assert [item for item in found if item.number == 120 and "no session open" in item.what] == []


def test_a_session_on_the_same_number_in_another_repository_does_not_hold_the_story():
    pulses = [_pulse("120", "a", repo="acme/gadgets", live=True)]
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    assert [item for item in found if item.number == 120 and item.what == "In Progress, no session open"]


def test_a_stale_transcript_does_not_hide_an_abandoned_story():
    """Without the running list, an old transcript is no evidence that anyone is there."""
    pulses = [_pulse("120", "a", idle=3 * 3600.0)]
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    assert [item for item in found if item.number == 120 and item.what == "In Progress, no session open"]


def test_two_running_sessions_idle_for_hours_are_not_a_duplicate():
    pulses = [_pulse("117", "a", idle=5 * 3600.0, live=True), _pulse("117", "b", idle=6 * 3600.0, live=True)]
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    assert not [item for item in found if item.number == 117 and "both on it" in item.what]


def test_a_story_two_sessions_share_is_reported():
    pulses = [_pulse("117", "a"), _pulse("117", "b")]
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    entry = next(item for item in found if item.number == 117)
    assert entry.what == "sessions a and b are both on it"


def test_a_long_idle_session_is_not_counted_as_a_duplicate():
    """A closed window keeps its row until it ages out; it must not ask for a second closing."""
    pulses = [_pulse("117", "a", idle=60.0), _pulse("117", "b", idle=3 * 3600.0)]
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    assert not [item for item in found if item.number == 117 and "both on it" in item.what]


def test_two_recently_active_sessions_are_still_a_duplicate():
    """The rule earns its place only while it still fires for the case it was written for."""
    pulses = [_pulse("117", "a", idle=60.0), _pulse("117", "b", idle=120.0)]
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=pulses)
    assert [item for item in found if item.number == 117 and "both on it" in item.what]


def test_a_pull_request_behind_main_names_the_command():
    behind = {("acme/widgets", 117)}
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=behind, pulses=[_pulse("117")])
    entry = next(item for item in found if item.number == 117)
    assert entry.fix == "run next 117"


def test_a_drafted_issue_that_never_reached_the_board_is_an_anomaly():
    missing = [("acme/widgets", 281, "https://github.com/acme/widgets/issues/281")]
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, set(), [], missing)
    entry = next(item for item in found if item.number == 281)
    assert (entry.what, entry.fix) == ("drafted, but not on the board", "captain apply --repair")


def test_a_fleet_that_agrees_with_its_logs_has_no_status_anomaly():
    kept = [story for story in fleet.stories(_nodes()) if story.number != 268]
    found = anomalies.find(kept, BLOCKERS, behind=set(), pulses=[_pulse("117"), _pulse("120")])
    # the fixture's stories predate the Feature field, which the row of its own covers
    assert [item.number for item in found if item.what != "in no feature"] == []


def test_a_finished_story_is_no_anomaly_and_needs_no_repair():
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=[_pulse("120")])
    assert [item.number for item in found if item.number == 117 and item.what != "in no feature"] == []


def test_a_teammates_story_in_flight_is_not_the_readers_anomaly():
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=[], me="ariav")
    assert not [item for item in found if item.number == 120 and "no session open" in item.what]


def test_the_readers_own_story_in_flight_is_still_reported():
    found = anomalies.find(fleet.stories(_nodes()), BLOCKERS, behind=set(), pulses=[], me="mjm")
    assert [item for item in found if item.number == 120 and "no session open" in item.what]


def test_a_closed_story_left_in_an_earlier_column_is_an_anomaly():
    """Only a closed story reaches the last two columns, so closed anywhere else is two facts disagreeing."""
    found = anomalies.find(fleet.stories(_closed("In Review")), {}, set(), [])

    assert [(a.number, a.what, a.fix) for a in found] == [(301, "closed, but In Review", "run next 301")]


def test_a_story_closed_as_not_planned_is_sent_to_withdraw():
    nodes = _closed("Draft")
    nodes[0]["content"]["stateReason"] = "NOT_PLANNED"

    found = anomalies.find(fleet.stories(nodes), {}, set(), [])

    assert [(a.what, a.fix) for a in found] == [
        ("closed, but Draft", 'captain apply --withdraw acme/widgets#301 --note "<why>"')
    ]


def test_a_closed_story_in_verification_is_not_an_anomaly():
    assert anomalies.find(fleet.stories(_closed("Verification")), {}, set(), []) == []


def test_a_closed_story_in_verification_is_no_anomaly():
    """Items left after the merge is a real place to be, not a fault."""
    assert anomalies.find(fleet.stories(_closed("Verification", OWED)), {}, set(), []) == []


def test_an_issue_the_process_never_touched_is_not_judged():
    found = anomalies.find(fleet.stories(_foreign("In Review")), {}, set(), [])
    assert found == []


def test_a_loop_through_a_draft_names_every_story_on_it():
    blockers = {
        ("acme/widgets", 257): [("acme/widgets", 268, "Domains")],
        ("acme/widgets", 268): [("acme/widgets", 253, "Seed")],
        ("acme/widgets", 253): [("acme/widgets", 257, "Authorize")],
    }
    found = anomalies.find(fleet.stories(_nodes()), blockers, behind=set(), pulses=[])
    loops = [item for item in found if "circle" in item.what]
    assert [(item.number, item.what) for item in loops] == [
        (253, "blockers run in a circle: #253 -> #257 -> #268 -> #253")
    ]
    assert loops[0].fix == "captain apply --unblock acme/widgets#253 --by acme/widgets#257"


def test_a_loop_across_repositories_is_one_row_with_one_command():
    here, there = _story(253), dataclasses.replace(_story(13), repo="acme/gadgets")
    blockers = {
        here.key: [("acme/gadgets", 13, "Elsewhere")],
        there.key: [("acme/widgets", 253, "Seed")],
    }
    loops = [item for item in anomalies.find([here, there], blockers, set(), []) if "circle" in item.what]
    assert [(item.repo, item.number) for item in loops] == [("acme/gadgets", 13)]
    assert loops[0].what == "blockers run in a circle: #13 -> acme/widgets#253 -> #13"
    assert loops[0].fix == "captain apply --unblock acme/gadgets#13 --by acme/widgets#253"


def test_an_archived_story_the_process_owns_is_an_anomaly():
    """Archiving hides a story from every board read, so the one deckhand still owns has to be named."""
    archived = [dataclasses.replace(_story(120), archived=True)]
    found = anomalies.find([], {}, set(), [], archived=archived)
    assert [(a.number, a.what, a.fix) for a in found] == [
        (120, "archived, and still In Progress", "captain apply --repair")
    ]


def test_an_archived_story_that_is_done_is_not_an_anomaly():
    """Archiving finished work is what the project's own workflow is for."""
    archived = [dataclasses.replace(fleet.stories(_closed("Done"))[0], archived=True)]
    assert anomalies.find([], {}, set(), [], archived=archived) == []


def test_an_archived_issue_the_process_never_wrote_to_is_not_an_anomaly():
    """A board carries issues that are not deckhand's, and archiving one of those is nobody's business."""
    archived = [dataclasses.replace(fleet.stories(_foreign("Backlog"))[0], archived=True)]
    assert anomalies.find([], {}, set(), [], archived=archived) == []


def test_a_story_shed_by_a_feature_s_story_and_left_out_is_a_stray():
    shedder, kid = _logged(1, SPLIT, feature="OPT_A"), _logged(2)

    assert anomalies.strays([shedder, kid]) == [(kid, shedder)]


def test_a_story_shed_by_a_story_in_no_feature_is_not_a_stray():
    assert anomalies.strays([_logged(1, SPLIT), _logged(2)]) == []


def test_a_shed_story_already_in_a_feature_is_not_a_stray():
    assert anomalies.strays([_logged(1, SPLIT, feature="OPT_A"), _logged(2, feature="OPT_B")]) == []


def test_a_story_parked_from_a_feature_s_story_and_left_out_is_a_stray():
    parent, kid = _logged(1, feature="OPT_A"), _logged(2, "Drafted: parked from #1")

    assert anomalies.strays([parent, kid]) == [(kid, parent)]


def test_a_story_parked_from_another_repository_s_featured_story_is_a_stray():
    parent = _logged(1, feature="OPT_A", repo="acme/gadgets")
    kid = _logged(2, "Drafted: parked from acme/gadgets#1")

    assert anomalies.strays([parent, kid]) == [(kid, parent)]


def test_a_story_parked_from_a_story_in_no_feature_is_not_a_stray():
    assert anomalies.strays([_logged(1), _logged(2, "Drafted: parked from #1")]) == []


def test_a_story_parked_from_an_archived_featured_story_is_a_stray():
    parent, kid = _logged(1, feature="OPT_A", closed=True), _logged(2, "Drafted: parked from #1")

    assert anomalies.strays([kid], archived=[parent]) == [(kid, parent)]


def test_a_note_that_mentions_parking_names_no_parent():
    assert anomalies.strays([_logged(1, feature="OPT_A"), _logged(2, "Noted: parked from #1")]) == []


def test_a_parked_stray_is_not_called_split():
    parent, kid = _logged(1, feature="OPT_A"), _logged(2, "Drafted: parked from #1")
    found = anomalies.find([parent, kid], {}, behind=set(), pulses=[])

    (row,) = [item for item in found if item.number == 2]
    assert row.what.startswith("split or parked from a story of")


def test_a_finished_shed_story_is_not_a_stray():
    assert anomalies.strays([_logged(1, SPLIT, feature="OPT_A"), _logged(2, closed=True)]) == []


def test_a_shed_issue_deckhand_does_not_own_is_not_a_stray():
    assert anomalies.strays([_logged(1, SPLIT, feature="OPT_A"), _logged(2, labels=())]) == []


def test_a_stray_is_an_anomaly_that_names_its_fix():
    found = anomalies.find([_logged(1, SPLIT, feature="OPT_A"), _logged(2)], {}, set(), [])

    assert [(item.number, item.what, item.fix) for item in found] == [
        (2, "split or parked from a story of Alpha, but in no feature", "epic add OPT_A acme/widgets#2")
    ]


def test_a_stray_fix_names_the_option_id_whatever_the_feature_is_called():
    shedder = dataclasses.replace(_logged(1, SPLIT, feature="OPT_A"), feature_name='Say "hi" | $HOME `id`')

    (found,) = anomalies.find([shedder, _logged(2)], {}, set(), [])

    assert found.what == 'split or parked from a story of Say "hi" \\| $HOME `id`, but in no feature'
    assert found.fix == "epic add OPT_A acme/widgets#2"


def test_a_stray_in_another_repository_names_its_ref_in_full():
    gadgets = _logged(4, "Split: #5 Follow on.", feature="OPT_A", repo="acme/gadgets")

    found = anomalies.find([gadgets, _logged(5, repo="acme/gadgets")], {}, set(), [])

    assert [item.fix for item in found] == ["epic add OPT_A acme/gadgets#5"]


def test_a_stray_named_in_another_case_is_still_found():
    gadgets = _logged(4, "Split: into Acme/Gadgets#5", feature="OPT_A", repo="acme/gadgets")

    found = anomalies.find([gadgets, _logged(5, repo="acme/gadgets")], {}, set(), [])

    assert [item.fix for item in found] == ["epic add OPT_A acme/gadgets#5"]


def test_an_archived_story_that_shed_still_reports_its_open_kid():
    shedder = dataclasses.replace(_logged(1, SPLIT, feature="OPT_A", closed=True), archived=True)
    kid = _logged(2)

    assert anomalies.strays([kid], [shedder]) == [(kid, shedder)]
    assert [item.number for item in anomalies.find([kid], {}, set(), [], archived=[shedder])] == [2]


def test_an_archived_kid_is_not_a_stray():
    kid = dataclasses.replace(_logged(2), archived=True)

    assert anomalies.strays([_logged(1, SPLIT, feature="OPT_A")], [kid]) == []


def test_a_kid_shed_by_two_stories_of_a_feature_is_one_stray():
    first = _logged(1, "Split: #3 Follow on, blocked by this story.", feature="OPT_A")
    second = _logged(2, "Split: #3 Follow on, blocked by this story.", feature="OPT_A")
    kid = _logged(3)

    assert anomalies.strays([first, second, kid]) == [(kid, first)]


def _unfiled(found: list) -> list[tuple[int, str, str]]:
    return [(item.number, item.what, item.fix) for item in found if item.what == "in no feature"]


def test_an_open_story_in_no_feature_is_named_with_the_epic_add_to_run():
    for status in ("Draft", "Refinement", "Backlog", "In Progress"):
        story = dataclasses.replace(_logged(4), status=status)
        assert _unfiled(anomalies.find([story], {}, set(), [])) == [
            (4, "in no feature", "epic add <feature> acme/widgets#4")
        ], status


def test_a_featured_finished_archived_or_foreign_story_is_not_named_for_its_feature():
    quiet = [
        _logged(1, feature="OPT_A"),
        _logged(2, closed=True),
        dataclasses.replace(_logged(3), archived=True),
        _logged(4, labels=()),
    ]
    assert _unfiled(anomalies.find(quiet, {}, set(), [])) == []


def test_the_stray_row_names_a_parked_child_instead():
    parent, kid = _logged(1, "Split: into #2", feature="OPT_PAY"), _logged(2)
    found = anomalies.find([parent, kid], {}, set(), [])
    assert _unfiled(found) == []
    assert any("split or parked from a story of" in item.what for item in found)
