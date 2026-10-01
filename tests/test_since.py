"""The snapshot a full captain read leaves, and what changed against the one before."""

from __future__ import annotations

import json
import time

from deckhand import since

BEFORE = {
    "at": 1000.0,
    "stories": {"acme/widgets#259": {"status": "In Review", "closed": False}},
    "sessions": {"ce54": {"story": "259", "waiting": False}},
    "anomalies": ["acme/widgets#264 In Progress, no session open"],
}


def _after(**changes):
    data = json.loads(json.dumps(BEFORE))
    data["at"] = 1000.0 + 3 * 3600
    data.update(changes)
    return data


def test_a_moved_story_is_one_line():
    after = _after(stories={"acme/widgets#259": {"status": "Verification", "closed": True}})
    assert "#259 In Review -> Verification, closed" in since.changes(BEFORE, after, "acme/widgets")


def test_a_new_story_is_named():
    stories = {**BEFORE["stories"], "acme/widgets#299": {"status": "Draft", "closed": False}}
    assert "#299 appeared in Draft" in since.changes(BEFORE, _after(stories=stories), "acme/widgets")


def test_a_story_seen_before_its_column_was_written_says_so():
    """A park adds the item, then writes Draft; a read between the two sees no column."""
    stories = {**BEFORE["stories"], "acme/widgets#306": {"status": "", "closed": False}}
    after = _after(stories=stories)
    assert "#306 appeared without a column" in since.changes(BEFORE, after, "acme/widgets")
    later = _after(stories={**stories, "acme/widgets#306": {"status": "Draft", "closed": False}})
    assert since.changes(after, later, "acme/widgets") == ["#306 no column -> Draft"]


def test_a_story_that_left_the_board_names_its_column():
    assert "#259 left the board (was In Review)" in since.changes(BEFORE, _after(stories={}), "acme/widgets")


def test_a_story_only_closed_or_reopened_says_so():
    after = _after(stories={"acme/widgets#259": {"status": "In Review", "closed": True}})
    assert since.changes(BEFORE, after, "acme/widgets") == ["#259 closed"]
    assert since.changes(after, BEFORE, "acme/widgets") == ["#259 reopened"]


def test_sessions_that_open_close_or_start_waiting():
    after = _after(sessions={"ce54": {"story": "259", "waiting": True}, "f7a9": {"story": "295", "waiting": False}})
    lines = since.changes(BEFORE, after, "acme/widgets")
    assert "session ce54 on #259 is waiting on you" in lines
    assert "session f7a9 opened on #295" in lines
    lines = since.changes(BEFORE, _after(sessions={}), "acme/widgets")
    assert "session ce54 on #259 closed" in lines


def test_a_session_that_stops_waiting_was_answered():
    waiting = _after(sessions={"ce54": {"story": "259", "waiting": True}})
    assert since.changes(waiting, _after(), "acme/widgets") == ["session ce54 on #259 answered"]


def test_a_session_on_no_story_is_free_without_a_number():
    after = _after(sessions={**BEFORE["sessions"], "f7a9": {"story": "free", "waiting": False}})
    assert since.changes(BEFORE, after, "acme/widgets") == ["session f7a9 opened on free"]


def test_anomalies_new_and_cleared():
    after = _after(anomalies=["acme/widgets#260 two sessions on one story"])
    lines = since.changes(BEFORE, after, "acme/widgets")
    assert "new: #260 two sessions on one story" in lines
    assert "cleared: #264 In Progress, no session open" in lines


def test_nothing_changed_is_empty():
    assert since.changes(BEFORE, _after(), "acme/widgets") == []


def test_a_saved_snapshot_loads_back(tmp_path):
    path = tmp_path / "captain.json"
    since.save(path, BEFORE)
    assert since.load(path) == BEFORE


def test_a_missing_or_malformed_snapshot_is_none(tmp_path):
    assert since.load(tmp_path / "absent.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert since.load(bad) is None
    story = BEFORE["stories"]["acme/widgets#259"]
    shapes = [
        [],
        {**BEFORE, "at": "noon"},
        {**BEFORE, "at": True},
        {**BEFORE, "stories": []},
        {**BEFORE, "stories": {"acme/widgets#259": "In Review"}},
        {**BEFORE, "stories": {"acme/widgets#259": {**story, "status": 3}}},
        {**BEFORE, "stories": {"acme/widgets#259": {**story, "closed": "no"}}},
        {**BEFORE, "sessions": None},
        {**BEFORE, "sessions": {"ce54": ["259"]}},
        {**BEFORE, "sessions": {"ce54": {"story": 259, "waiting": False}}},
        {**BEFORE, "sessions": {"ce54": {"story": "259"}}},
        {**BEFORE, "anomalies": "none"},
        {**BEFORE, "anomalies": [1]},
    ]
    for shape in shapes:
        bad.write_text(json.dumps(shape), encoding="utf-8")
        assert since.load(bad) is None, shape


def test_the_heading_says_when():
    assert since.heading(BEFORE, _after()["at"]).startswith("Since ")
    assert "3 hours ago" in since.heading(BEFORE, _after()["at"])


def test_a_day_or_more_heads_with_the_day():
    later = BEFORE["at"] + 30 * 3600
    assert since.heading(BEFORE, later) == time.strftime("Since %a %H:%M, 30 hours ago", time.localtime(BEFORE["at"]))
