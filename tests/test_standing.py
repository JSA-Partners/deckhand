from __future__ import annotations

import pytest

from deckhand import board, brief, fleet, standing
from tests.test_board import NOW, TODAY, at, item, sample, session, stage, story


def filed_hand(name: str, ref: str, state: str) -> dict:
    return {"name": name, "ref": ref, "state": state, "waiting_for": None, "started_at": at(30)}


HANDS = [
    filed_hand("hand-ACME-14", "ACME-14", "stopped"),
    filed_hand("hand-ACME-15", "ACME-15", "working"),
    filed_hand("hand-ACME-17", "ACME-17", "blocked"),
    filed_hand("hand-OTHER-9", "OTHER-9", "working"),
    filed_hand("spike-k1", "k1", "working"),
]
SESSIONS = [
    session("a", "ACME-14", state="stopped", status=None),
    session("b", "ACME-15"),
    session("c", "ACME-17", state="blocked", status="waiting"),
    session("d", "OTHER-9"),
    fleet.Session("e", "spike-k1", "sid-e", "/w", at(30), "working", "busy", None),
]


def quiet() -> dict:
    return sample(items=[], hands=HANDS)


def kinds(status: dict, hands: dict[str, standing.Hand]) -> list[tuple[str, str]]:
    return [(entry.kind, entry.ref) for entry in standing.classify(status, hands, TODAY)]


def test_each_story_lands_in_one_place_in_build_order():
    assert kinds(quiet(), standing.filed(quiet())) == [
        (standing.WORKING, "ACME-15"),
        (standing.WORKING, "ACME-17"),
        (standing.WORKING, "k1"),
        (standing.STOPPED, "ACME-14"),
        (standing.QUEUED, "ACME-16"),
        (standing.QUEUED, "ACME-18"),
        (standing.QUEUED, "ACME-19"),
        (standing.MERGED, "ACME-13"),
    ]


def test_an_item_puts_its_story_under_needs_you_oldest_first():
    found = kinds(sample(hands=HANDS), standing.filed(quiet()))
    assert found[:2] == [(standing.NEEDS, "ACME-14"), (standing.NEEDS, "ACME-16")]
    assert (standing.STOPPED, "ACME-14") not in found


def test_without_a_day_start_no_merge_is_listed():
    assert standing.MERGED not in {kind for kind, _ in kinds_without_today()}


def kinds_without_today() -> list[tuple[str, str]]:
    return [(entry.kind, entry.ref) for entry in standing.classify(quiet(), standing.filed(quiet()))]


def test_the_live_read_and_the_file_classify_alike():
    assert kinds(quiet(), standing.live(SESSIONS)) == kinds(quiet(), standing.filed(quiet()))


def test_a_live_hand_keeps_its_id_and_status():
    hand = standing.live(SESSIONS)["ACME-17"]
    assert (hand.id, hand.state, hand.status, hand.spike) == ("c", "blocked", "waiting", False)
    assert standing.live(SESSIONS)["k1"].spike


def counts_on_every_view(status: dict) -> tuple[str, str, dict[str, int]]:
    quiet_line = brief.text(status, NOW, brief.UNICODE).split("\n\n")[-1]
    line = brief.line(status, NOW, brief.UNICODE)
    found = board.build(status, None, NOW, TODAY, brief.UNICODE)
    return quiet_line, line, {section.name: len(section.rows) for section in found.sections}


def test_the_brief_the_status_line_and_the_board_agree():
    quiet_line, line, sections = counts_on_every_view(quiet())
    assert quiet_line.startswith("◐ 3 working · × 1 stopped · ○ 3 queued · nothing needs you")
    assert line == "◐ 3 working · nothing needs you"
    assert sections == {"needs you": 0, "working": 3, "stopped": 1, "queued": 3, "done today": 1}
    quiet_line, line, sections = counts_on_every_view(sample(hands=HANDS))
    assert quiet_line.startswith("◐ 3 working · ○ 2 queued · slots")
    assert line.startswith("● 2 need you")
    assert sections == {"needs you": 2, "working": 3, "queued": 2, "done today": 1}


@pytest.mark.parametrize("bad", [7, None, "x", [], {}, [7], {"ref": 7}])
def test_a_wrong_type_anywhere_is_skipped_not_fatal(bad):
    for key in ("items", "stories", "hands", "slots", "budget", "written_at", "stale_since", "feature"):
        standing.classify(sample(**{key: bad}), standing.filed(sample(**{key: bad})), TODAY)
    for field in ("ref", "state", "blocked_by", "stage", "completed_at", "title", "owner", "mine"):
        status = sample(stories=[{**sample()["stories"][3], field: bad}], hands=[{"ref": bad, "state": bad}])
        standing.classify(status, standing.filed(status), TODAY)


def test_a_started_story_with_no_live_hand_reads_stopped():
    found = kinds(sample(items=[]), standing.live([session("x", "ACME-17")]))
    assert found == [
        (standing.WORKING, "ACME-17"),
        (standing.STOPPED, "ACME-14"),
        (standing.STOPPED, "ACME-15"),
        (standing.QUEUED, "ACME-16"),
        (standing.QUEUED, "ACME-18"),
        (standing.QUEUED, "ACME-19"),
        (standing.MERGED, "ACME-13"),
    ]


def test_a_failed_spike_off_the_board_surfaces():
    spikes = [
        fleet.Session("f", "spike-k9", "sid-f", "/w", at(9), "failed", None, None),
        fleet.Session("g", "spike-k8", "sid-g", "/w", at(9), "done", None, None),
    ]
    found = kinds(sample(items=[], stories=[]), standing.live(spikes))
    assert found == [(standing.STOPPED, "k9")]


@pytest.mark.parametrize("at_edge", ["9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00+14:00"])
def test_a_time_out_of_range_reads_as_no_time(at_edge):
    assert standing.timestamp(at_edge) is None


def teammates() -> dict:
    stories = [
        story("ACME-20", "Receipts", owner="Sam", mine=False, stage=stage("checks", 5)),
        story("ACME-21", "Refunds", "Backlog", owner="Sam", mine=False),
    ]
    return sample(items=[], stories=stories, hands=[])


def test_a_teammates_story_is_theirs_not_stopped_or_queued():
    assert kinds(teammates(), standing.filed(teammates())) == [(standing.TEAM, "ACME-20"), (standing.TEAM, "ACME-21")]
    quiet_line, line, sections = counts_on_every_view(teammates())
    assert quiet_line.startswith("◐ 0 working · ○ 0 queued · nothing needs you")
    assert line == "◐ 0 working · nothing needs you"
    assert sections == {"needs you": 0, "team": 2}


def test_a_teammates_story_with_a_hand_here_or_an_item_is_this_persons():
    hands = standing.live([session("x", "ACME-20")])
    assert kinds(teammates(), hands)[0] == (standing.WORKING, "ACME-20")
    waiting = teammates() | {"items": [item("k20", "second-sign-off", "ACME-20", "Receipts", 2, "next")]}
    assert kinds(waiting, {})[0] == (standing.NEEDS, "ACME-20")


def test_a_stale_state_reads_stale_on_every_view():
    status = quiet() | {"written_at": at(5), "stale_since": at(1)}
    quiet_line, line, _ = counts_on_every_view(status)
    hint = board.build(status, None, NOW, TODAY, brief.UNICODE).quiet
    assert "nothing was waiting as of 5m ago" in quiet_line
    assert hint == "nothing was waiting as of 5m ago"
    assert line == "○ captain stale 5m"


def test_an_unknown_person_is_never_an_all_clear_on_any_view():
    status = quiet() | {"person_unknown": True}
    shown, line, _ = counts_on_every_view(status)
    found = board.build(status, None, NOW, TODAY, brief.UNICODE)
    assert brief.text(status, NOW, brief.UNICODE).split("\n\n")[0] == brief.UNKNOWN
    assert "nothing needs you" not in shown
    assert line == found.lead[0] + " " + found.lead[1] == "● personal key unreadable · run /deckhand:setup"
    assert found.quiet == brief.UNKNOWN
