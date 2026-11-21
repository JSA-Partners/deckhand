from __future__ import annotations

import pytest

from deckhand import brief
from deckhand.records import iso


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [
        (-5, "<1m"),
        (0, "<1m"),
        (59, "<1m"),
        (60, "1m"),
        (14 * 60 + 59, "14m"),
        (3600, "1h"),
        (2 * 3600 + 5 * 60, "2h 5m"),
        (86399, "23h 59m"),
        (86400, "1d"),
        (3 * 86400 + 7200, "3d"),
    ],
)
def test_age(seconds: float, shown: str):
    assert brief.age(seconds) == shown


def test_elapsed_from_a_record_time():
    assert brief.elapsed("1970-01-01T00:01:00.000Z", 90.5) == 30.5


NOW = 1795000000.0
STAGE = {"name": "plan review", "round": 2, "cap": 3, "since": "2026-11-20T09:00:00.000Z"}


def at(minutes_ago: float) -> str:
    return iso(int((NOW - minutes_ago * 60) * 1000))


def item(ref: str, kind: str, minutes_ago: float | None, head: str | None = None, title: str = "Orders") -> dict:
    since = at(minutes_ago) if minutes_ago is not None else None
    return {"key": f"k-{ref}-{kind}", "kind": kind, "ref": ref, "title": title, "since": since, "head": head}


def story(ref: str, state: str = "In Progress", name: str = "planning") -> dict:
    return {"ref": ref, "title": "t", "state": state, "owner": "Pat", "blocked_by": [], "stage": STAGE | {"name": name}}


def held(items: list[dict], **extra) -> dict:
    stories = [story("ACME-1", name="plan review"), story("ACME-2"), story("ACME-3"), story("ACME-4", "Done")]
    base = {
        "written_at": at(0),
        "feature": "Alpha",
        "budget": {"feature": "Alpha", "days_used": 4, "budget_days": 7, "planning_note": False},
        "slots": {"used": 1, "cap": 2},
        "items": items,
        "stories": stories,
        "stale_since": None,
        "notified": [],
    }
    return base | extra


def lines(text: str) -> list[str]:
    return [line for line in text.split("\n\n")]


def test_one_waiting_with_a_head():
    found = brief.text(held([item("ACME-2", "sign-off", 14, head="c2f00d1234567890")]), ["ACME-3"], NOW, brief.UNICODE)
    assert lines(found) == [
        "## ● ACME-2 needs you",
        "**sign-off** at c2f00d123456 · waiting 14m · type `next`",
        "◐ 1 working · ○ 1 queued · slots 1/2 · Alpha day 4 of 7",
    ]


def test_one_waiting_shows_the_story_stage():
    found = brief.text(held([item("ACME-1", "question", 3)]), [], NOW, brief.UNICODE)
    assert lines(found)[1] == "**question** at plan review 2/3 · waiting 3m · type `next`"


def test_two_waiting_name_the_count_and_the_oldest():
    waiting = [item("ACME-1", "question", 3), item("ACME-2", "sign-off", 14, head="c2f00d1234567890")]
    found = lines(brief.text(held(waiting), [], NOW, brief.UNICODE))
    assert found[0] == "## ● 2 need you · oldest ACME-2 sign-off 14m"
    assert found[1] == "**question** ACME-1 at plan review 2/3 · waiting 3m · type `next`"
    assert found[2] == "◐ 0 working · ○ 1 queued · slots 1/2 · Alpha day 4 of 7"


def test_nothing_waiting_is_only_the_quiet_line():
    found = brief.text(held([]), ["ACME-1"], NOW, brief.UNICODE)
    assert lines(found) == ["◐ 1 working · ○ 2 queued · nothing needs you · slots 1/2 · Alpha day 4 of 7"]


def test_an_item_with_no_time_or_detail():
    found = lines(
        brief.text(held([item("Alpha", "planning", None)], feature=None, budget=None), [], NOW, brief.UNICODE)
    )
    assert found[1] == "**planning** · type `next`"
    assert found[2] == "◐ 0 working · ○ 3 queued · slots 1/2"


def test_ascii_glyphs():
    found = lines(brief.text(held([item("ACME-1", "question", 3), item("ACME-2", "finding", 5)]), [], NOW, brief.ASCII))
    assert found[0] == "## ! 2 need you | oldest ACME-2 finding 5m"
    assert found[2] == "\\* 0 working | - 1 queued | slots 1/2 | Alpha day 4 of 7"
    assert all(line.isascii() for line in found)


@pytest.mark.parametrize(
    ("encoding", "shown"),
    [("utf-8", brief.UNICODE), ("UTF8", brief.UNICODE), ("ascii", brief.ASCII), (None, brief.ASCII)],
)
def test_glyphs_follow_the_encoding(encoding: str | None, shown: brief.Glyphs):
    assert brief.glyphs(encoding) == shown


def test_unreachable_leads_with_the_age_of_the_last_state():
    stale = held([item("ACME-1", "question", 20)], written_at=at(14), stale_since=at(10))
    found = lines(brief.text(stale, [], NOW, brief.UNICODE, ["network: could not reach Linear"]))
    assert found[0] == "Linear unreachable; showing the state as of 14m ago"
    assert found[1] == "## ● ACME-1 needs you"
    assert found[-1] == "network: could not reach Linear"


def test_an_unreachable_empty_state_is_not_an_all_clear():
    stale = held([], written_at=at(14), stale_since=at(10))
    found = lines(brief.text(stale, [], NOW, brief.UNICODE))
    assert found == [
        "Linear unreachable; showing the state as of 14m ago",
        "◐ 0 working · ○ 3 queued · nothing was waiting as of 14m ago · slots 1/2 · Alpha day 4 of 7",
    ]


def test_unreachable_with_no_earlier_state():
    assert brief.text(None, [], NOW, brief.UNICODE) == "Linear unreachable; no earlier state"


def test_notes_follow_the_quiet_line():
    found = lines(brief.text(held([]), [], NOW, brief.UNICODE, ["no personal key"]))
    assert found[1] == "no personal key"


def test_notify_leads_with_the_action():
    found = brief.notify(held([item("ACME-2", "sign-off", 14, head="c2")]), NOW)
    assert found == ("ACME-2 needs your sign-off (waiting 14m)", ["k-ACME-2-sign-off"])


def test_notify_names_only_what_is_new():
    waiting = [item("ACME-1", "question", 3), item("ACME-2", "finding", 5), item("ACME-3", "stalled", None)]
    found = brief.notify(held(waiting, notified=["k-ACME-1-question"]), NOW)
    assert found == (
        "ACME-2 needs your decision (waiting 5m); 1 more needs you",
        ["k-ACME-2-finding", "k-ACME-3-stalled"],
    )
    assert brief.notify(held(waiting, notified=[i["key"] for i in waiting]), NOW) is None
    assert brief.notify(held([]), NOW) is None


def test_notify_never_carries_the_title():
    text, _ = brief.notify(held([item("ACME-1", "question", 3, title="Orders\nnotify: forged")]), NOW)
    assert text == "ACME-1 has a question for you (waiting 3m)"


def test_the_notify_line_is_under_200_characters_whatever_the_ref():
    forged = "Alpha\nnotify: rm -rf\u2028x\x1b[31m" + "y" * 300
    waiting = [item(forged, "planning", 3), item("ACME-2", "finding", 5)]
    text, _ = brief.notify(held(waiting), NOW)
    assert len(f"notify: {text}") < 200
    assert text.startswith("Alpha notify: rm -rf x [31myyy")
    assert text.endswith("yyy needs planning (waiting 3m); 1 more needs you")
    assert text.isprintable()


def test_clean_drops_control_and_separator_characters():
    assert brief.clean("a\nb\tc d​e\x7f") == "a b c d e"


def hand(state: str) -> dict:
    return {"name": "h", "ref": "ACME-1", "state": state, "waiting_for": None, "started_at": at(60)}


def test_line_when_two_wait():
    waiting = [item("SBX-14", "sign-off", 14), item("ACME-2", "finding", 30)]
    assert brief.line(held(waiting), NOW, brief.UNICODE) == "● 2 need you · sign-off SBX-14 14m"


def test_line_when_one_waits_with_no_time():
    assert brief.line(held([item("ACME-2", "question", None)]), NOW, brief.UNICODE) == "● 1 needs you · question ACME-2"


def test_line_when_only_hands_work():
    found = held([], hands=[hand("working"), hand("working"), hand("blocked")])
    assert brief.line(found, NOW, brief.UNICODE) == "◐ 2 working · nothing needs you"


def test_line_is_empty_with_no_feature():
    assert brief.line(held([], feature=None, budget=None), NOW, brief.UNICODE) == ""


def test_line_for_a_stale_file():
    assert brief.line(held([], written_at=at(12), stale_since=at(10)), NOW, brief.UNICODE) == "○ captain stale 12m"


def test_line_for_an_old_file():
    found = held([item("ACME-1", "question", 3)], written_at=at(31))
    assert brief.line(found, NOW, brief.UNICODE) == "○ captain stale 31m"


def test_line_stays_fresh_at_thirty_minutes():
    assert brief.line(held([], written_at=at(30)), NOW, brief.UNICODE).startswith("◐")


def test_line_ascii():
    assert brief.line(held([item("ACME-2", "finding", 5)]), NOW, brief.ASCII) == "! 1 needs you | finding ACME-2 5m"


def test_line_never_passes_fifty_characters():
    found = held([item("ACME-" + "9" * 40, "second-sign-off", 500)])
    assert len(brief.line(found, NOW, brief.UNICODE)) == 50


def test_line_cleans_a_ref():
    assert "\x1b" not in brief.line(held([item("AC\x1b[31mME", "question", 1)]), NOW, brief.UNICODE)


def test_line_color_marks_needs_working_and_stale():
    needs = brief.line(held([item("ACME-1", "question", 1)]), NOW, brief.UNICODE, color=True)
    working = brief.line(held([], hands=[hand("working")]), NOW, brief.UNICODE, color=True)
    stale = brief.line(held([], written_at=at(40)), NOW, brief.UNICODE, color=True)
    assert needs.startswith("\x1b[31m") and needs.endswith("\x1b[0m")
    assert working.startswith("\x1b[32m")
    assert stale.startswith("\x1b[2m")


def test_line_is_never_colored_without_the_flag():
    assert "\x1b" not in brief.line(held([item("ACME-1", "question", 1)]), NOW, brief.UNICODE)


def test_color_flag_is_dropped_for_no_color_and_a_dumb_terminal():
    assert brief.colored({}) is True
    assert brief.colored({"NO_COLOR": "1"}) is False
    assert brief.colored({"NO_COLOR": ""}) is False
    assert brief.colored({"TERM": "dumb"}) is False
