from __future__ import annotations

import pytest

from deckhand import board, brief, fleet, standing
from tests.test_board import NOW, TODAY, at, sample, session


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
    for field in ("ref", "state", "blocked_by", "stage", "completed_at", "title", "owner"):
        status = sample(stories=[{**sample()["stories"][3], field: bad}], hands=[{"ref": bad, "state": bad}])
        standing.classify(status, standing.filed(status), TODAY)
