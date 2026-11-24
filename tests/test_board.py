from __future__ import annotations

import pytest

from deckhand import board, brief, fleet, frame
from deckhand.records import iso

NOW = 1795000000.0
TODAY = NOW - 10 * 3600
G = brief.ASCII


def at(minutes: float) -> str:
    return iso(int((NOW - minutes * 60) * 1000))


def story(ref: str, title: str, state: str = "In Progress", **extra) -> dict:
    found = {"ref": ref, "title": title, "state": state, "owner": "Pat", "blocked_by": [], "stage": None}
    return found | {"completed_at": None} | extra


def stage(name: str, minutes: float | None, round: int | None = None) -> dict:
    return {"name": name, "round": round, "since": at(minutes) if minutes else None}


def item(key: str, kind: str, ref: str, title: str, minutes: float, next: str) -> dict:
    return {"key": key, "kind": kind, "ref": ref, "title": title, "since": at(minutes), "next": [next], "head": None}


def sample(**changes) -> dict:
    found = {
        "shape": 1,
        "written_at": at(0.1),
        "team": "ACME",
        "feature": "Alpha",
        "budget": {"feature": "Alpha", "days_used": 2, "budget_days": 7, "planning_note": False},
        "slots": {"used": 2, "cap": 2},
        "items": [
            item("k16", "question", "ACME-16", "Rotate the access log", 3, "decide context question q1"),
            item("k14", "sign-off", "ACME-14", "Print every greeting", 14, "signoff context --story ACME-14"),
        ],
        "stories": [
            story("ACME-12", "Old work", "Done", completed_at=at(60 * 30)),
            story("ACME-13", "Add a farewell line", "Done", completed_at=at(120)),
            story("ACME-14", "Print every greeting", stage=stage("sign-off", 14)),
            story("ACME-15", "Add a farewell banner to the greeting script", stage=stage("branch review", 6, 2)),
            story("ACME-16", "Rotate the access log", stage=stage("planning", 20)),
            story("ACME-17", "Document the release steps", stage=stage("building", 1)),
            story("ACME-18", "Trim trailing spaces", "Backlog", stage=stage("drafting", None)),
            story("ACME-19", "Tidy the docs", "Backlog", blocked_by=["ACME-18"]),
        ],
        "hands": [
            {"name": "hand-ACME-15", "ref": "ACME-15", "state": "working", "waiting_for": None, "started_at": at(30)},
            {"name": "hand-ACME-17", "ref": "ACME-17", "state": "working", "waiting_for": None, "started_at": at(9)},
        ],
        "release": False,
        "stale_since": None,
    }
    return found | changes


def session(id: str, ref: str, state: str = "working", status: str | None = "busy", **extra) -> fleet.Session:
    fields = {"cwd": "/w", "started_at": at(30), "waiting_for": None} | extra
    return fleet.Session(id, f"hand-{ref}", f"sid-{id}", state=state, status=status, **fields)


CAPTAIN = fleet.Session("cap1", "captain", "sid-cap1", "/w", at(60), "", "idle", None)
LIVE = [
    CAPTAIN,
    session("a1b2", "ACME-15"),
    session("c3d4", "ACME-17", status="waiting"),
    session("e5f6", "ACME-18", state="stopped", status=None),
]


def shown(
    status: dict | None = None,
    sessions: list | None = LIVE,
    cols: int = 100,
    rows: int | None = None,
    failed: bool = False,
    **kw,
):
    read = fleet.Fleet([], "the fleet read failed: boom") if failed else None
    read = read or (fleet.Fleet(sessions) if sessions is not None else None)
    found = board.build(sample() if status is None else status, read, NOW, TODAY, G)
    return frame.render(found, G, cols, rows, **kw)


def texts(lines: list[frame.Line]) -> list[str]:
    return [line.text.rstrip() for line in lines]


def section(lines: list[frame.Line], name: str) -> list[str]:
    found = texts(lines)
    start = next(i for i, text in enumerate(found) if text.startswith(f"-- {name}  "))
    rest = found[start + 1 :]
    return rest[: next((i for i, text in enumerate(rest) if not text.startswith(" ")), len(rest))]


def rules(lines: list[frame.Line]) -> list[str]:
    return [" ".join(text.split()[1:-1]) for text in texts(lines) if text.startswith("-- ")]


def test_sections_come_in_order_with_their_counts():
    assert rules(shown()) == ["needs you 2", "working 2", "stopped 1", "queued 1", "done today 1"]


def test_the_header_leads_with_the_status_lines_answer():
    top = texts(shown())[0]
    assert top == " ! 2 need you | question ACME-16 3m | updated 6s ago | Alpha | day 2 of 7 | slots 2/2 | captain idle"
    quiet = texts(shown(sample(items=[])))[0]
    assert quiet == " * 2 working | nothing needs you | updated 6s ago | Alpha | day 2 of 7 | slots 2/2 | captain idle"
    assert quiet[1:].startswith(brief.line(sample(items=[], hands=sample()["hands"]), NOW, G))


def test_a_stale_header_reads_like_the_stale_status_line():
    head = texts(shown(sample(written_at=at(45))))[0]
    assert head == " - captain stale 45m | Alpha | day 2 of 7 | slots 2/2 | captain idle"


def test_needs_you_lists_the_oldest_first_with_kind_and_wait():
    rows = section(shown(), "needs you")
    assert rows[0].split() == ["!", "ACME-14", "Print", "every", "greeting", "sign-off", "14m"]
    assert rows[1].split() == ["!", "ACME-16", "Rotate", "the", "access", "log", "question", "3m"]
    assert rows[2] == "   the captain asks these one at a time"


def test_working_rows_show_the_stage_and_the_hands_own_status():
    rows = section(shown(), "working")
    assert rows[0].split()[:2] == ["*", "ACME-15"]
    assert rows[0].split()[-4:] == ["branch", "review", "2", "6m"]
    assert rows[1].split()[-3:] == ["building,", "waiting", "1m"]


def test_a_round_past_three_reads_its_number_alone():
    stories = sample()["stories"]
    stories[3] = stories[3] | {"stage": stage("branch review", 6, 4) | {"cap": 3}}
    rows = section(shown(sample(stories=stories)), "working")
    assert rows[0].split()[-4:] == ["branch", "review", "4", "6m"]


def test_a_story_whose_hand_is_gone_reads_stopped_at_its_stage():
    sessions = [session("a1b2", "ACME-15", state="failed", status=None), LIVE[3]]
    rows = section(shown(sessions=sessions), "stopped")
    assert [row.split()[:2] for row in rows] == [["x", "ACME-15"], ["x", "ACME-17"], ["x", "ACME-18"]]
    assert "failed at branch review 2" in rows[0]
    assert "stopped at building" in rows[1]
    assert "stopped at drafting" in rows[2]
    assert "ACME-15" not in "\n".join(section(shown(sessions=sessions), "queued"))


def test_queued_rows_have_no_hand_and_no_item():
    rows = section(shown(sample(hands=[]), sessions=None), "queued")
    assert [row.split()[1] for row in rows] == ["ACME-18", "ACME-19"]
    assert rows[0].endswith("waits for a slot")
    assert rows[1].endswith("blocked by ACME-18")


def test_a_free_slot_reads_ready():
    rows = section(shown(sample(slots={"used": 0, "cap": 3}), sessions=[]), "queued")
    assert rows[0].endswith("ready")


def test_done_today_counts_only_merges_since_midnight():
    rows = section(shown(), "done today")
    assert len(rows) == 1
    assert rows[0].split() == ["+", "ACME-13", "Add", "a", "farewell", "line", "merged", "2h"]


def test_empty_sections_other_than_needs_you_are_left_out():
    status = sample(stories=[story("ACME-14", "Print every greeting"), story("ACME-20", "Later")], hands=[])
    assert [rule.split()[0] for rule in rules(shown(status, sessions=[]))] == ["needs", "queued"]


def test_without_a_live_read_the_file_hands_are_working_and_no_attach_shows():
    lines = shown(sessions=None, selected="story:ACME-15", expanded="story:ACME-15")
    assert [row.split()[1] for row in section(lines, "working")[:1]] == ["ACME-15"]
    assert not any("claude attach" in text for text in texts(lines))


def test_a_failed_fleet_read_is_marked_in_the_header():
    assert texts(shown(failed=True))[1] == " - hands from the file, fleet read failed"
    assert not any("fleet read failed" in text for text in texts(shown()))
    assert section(shown(failed=True), "working")[0].split()[1] == "ACME-15"


def test_a_needs_row_says_the_captain_will_ask():
    lines = shown(selected="item:k16", expanded="item:k16")
    assert section(lines, "needs you")[2].strip() == "has a question for you; the captain will ask"


def test_details_say_the_action_in_words_and_paint_only_typed_commands():
    lines = shown(selected="item:k14", expanded="item:k14")
    assert section(lines, "needs you")[1].strip() == "needs your sign-off; the captain will ask"
    assert not any("signoff context" in text for text in texts(lines))
    lines = shown(selected="story:ACME-15", expanded="story:ACME-15")
    assert section(lines, "working")[1].split() == ["hand:", "claude", "attach", "a1b2"]
    styles = {text: style for line in lines for text, style in line.spans}
    assert styles["claude attach a1b2"] == "command"
    assert "command" not in {
        style for line in shown(selected="item:k14", expanded="item:k14") for _, style in line.spans
    }
    assert [line.selected for line in lines if "ACME-15" in line.text] == [True]


def test_an_unreachable_linear_is_the_only_stale_banner():
    assert not texts(shown())[1].strip()
    assert not texts(shown(sample(written_at=at(45))))[1].strip()
    banner = texts(shown(sample(written_at=at(5), stale_since=at(1))))[1]
    assert banner == " - stale: Linear is unreachable, this is the state from 5m ago"


def test_no_snapshot_says_linear_is_being_read():
    found = board.build(None, fleet.Fleet(LIVE), NOW, TODAY, G)
    lines = texts(frame.render(found, G, 100))
    assert lines[0] == " Captain"
    assert "   reading Linear for the first time" in lines


def test_nothing_waiting_says_so_with_the_working_count():
    assert section(shown(sample(items=[])), "needs you") == ["   nothing needs you; 2 working"]


def test_hand_and_file_text_is_cleaned():
    hostile = "Orders\x1b]0;owned\x07\nnext"
    status = sample(items=[], stories=[story("ACME-15", hostile, stage=stage("building", 1))])
    sessions = [session("a1", "ACME-15", status="waiting\x1b[2J", waiting_for="you\r\x1b[31m")]
    lines = shown(status, sessions, selected="story:ACME-15", expanded="story:ACME-15")
    joined = "\n".join(line.text for line in lines)
    assert "\x1b" not in joined and "\x07" not in joined and "\r" not in joined
    assert "Orders ]0;owned next" in joined


BAD = [7, None, "x", [], {}, [7], {"ref": 7}, 1.5, True]


@pytest.mark.parametrize("bad", BAD)
def test_a_wrong_type_anywhere_still_renders(bad):
    for key in sample():
        frame.text(shown(sample(**{key: bad}), rows=30, keys=True), color=True)
    for field in ("ref", "title", "state", "owner", "blocked_by", "stage", "completed_at"):
        for at_story in (2, 3, 6, 7):
            stories = sample()["stories"]
            stories[at_story] = stories[at_story] | {field: bad}
            frame.text(shown(sample(stories=stories), selected=f"story:{stories[at_story].get('ref')}"), color=False)
    for field in ("key", "kind", "ref", "title", "since", "next", "head"):
        items = [sample()["items"][0] | {field: bad}]
        frame.text(shown(sample(items=items), expanded="item:k16"), color=False)
    for field in ("name", "ref", "state", "waiting_for", "started_at"):
        hands = [sample()["hands"][0] | {field: bad}]
        frame.text(shown(sample(hands=hands), sessions=None), color=False)
    stage_bad = sample()["stories"]
    stage_bad[3] = stage_bad[3] | {"stage": {"name": "plan review", "round": bad, "since": bad}}
    frame.text(shown(sample(stories=stage_bad)), color=False)


def test_an_integer_blocked_by_is_skipped():
    stories = sample()["stories"]
    stories[7] = stories[7] | {"blocked_by": 7}
    assert section(shown(sample(stories=stories)), "queued")[0].endswith("waits for a slot")


def test_midnight_is_local():
    assert board.midnight(NOW) <= NOW < board.midnight(NOW) + 24 * 3600 + 3600


def test_only_spikes_join_working_without_a_story_and_foreign_hands_stay_off():
    spike = fleet.Session("s9", "spike-k1", "sid-s9", "/w", at(4), "working", "busy", None)
    rows = section(shown(sessions=[*LIVE, session("z9", "OTHER-9"), spike]), "working")
    assert [row.split()[1] for row in rows] == ["ACME-15", "ACME-17", "k1"]
    assert rows[-1].split()[2] == "spike"
    files = sample()
    files["hands"] += [{"name": "hand-OTHER-9", "ref": "OTHER-9", "state": "working"}]
    files["hands"] += [{"name": "spike-k2", "ref": "k2", "state": "working"}]
    rows = section(shown(files, sessions=None), "working")
    assert [row.split()[1] for row in rows] == ["ACME-15", "ACME-17", "k2"]


def test_the_header_counts_the_same_hands_as_the_body():
    lines = shown(sample(items=[]), sessions=[session("x", "ACME-17")])
    assert texts(lines)[0].startswith(" * 1 working | nothing needs you")
    assert rules(lines)[1] == "working 1"


def test_a_story_past_its_start_with_no_session_reads_stopped():
    rows = section(shown(sessions=[session("x", "ACME-17")]), "stopped")
    assert [row.split()[1] for row in rows] == ["ACME-15"]
    assert "stopped at branch review 2" in rows[0]


EDGES = ["9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00+14:00"]


@pytest.mark.parametrize("at_edge", EDGES)
def test_a_time_out_of_range_still_renders(at_edge):
    stories = sample()["stories"]
    stories[1] = stories[1] | {"completed_at": at_edge}
    stories[3] = stories[3] | {"stage": stage("branch review", 6, 2) | {"since": at_edge}}
    items = [sample()["items"][0] | {"since": at_edge}]
    for status in (sample(stories=stories, items=items), sample(written_at=at_edge), sample(stale_since=at_edge)):
        frame.text(shown(status, rows=30, keys=True, expanded="story:ACME-13"), color=True)


def test_a_teammates_story_shows_as_theirs_in_plain_text():
    stories = [story("ACME-20", "Receipts", owner="Sam", mine=False, stage=stage("checks", 5))]
    lines = shown(sample(items=[], stories=stories, hands=[]), sessions=[])
    assert rules(lines) == ["needs you 0", "team 1"]
    row = next(line for line in lines if "ACME-20" in line.text)
    assert row.text.split() == ["-", "ACME-20", "Receipts", "Sam", "|", "checks", "5m"]
    assert {style for text, style in row.spans if text.strip()} <= {"", "bold", "dim"}


def test_a_stale_board_says_what_was_waiting_not_an_all_clear():
    unreachable = section(shown(sample(items=[], written_at=at(5), stale_since=at(1))), "needs you")
    assert unreachable == ["   nothing was waiting as of 5m ago; 2 working"]
    assert section(shown(sample(items=[], written_at=at(45))), "needs you") == [
        "   nothing was waiting as of 45m ago; 2 working"
    ]
    assert section(shown(sample(items=[])), "needs you") == ["   nothing needs you; 2 working"]


def test_the_header_says_whether_a_captain_runs():
    assert texts(shown(sessions=LIVE[1:], cols=110))[0].endswith("| slots 2/2 | no captain running")
    busy = fleet.Session("cap2", "captain", "sid-cap2", "/w", at(1), "", "busy", None)
    assert texts(shown(sessions=[*LIVE, busy], cols=110))[0].endswith("| captain working")
    assert "captain" not in texts(shown(failed=True))[0].split("|")[-1]


def test_the_engine_tab_shows_the_engines_count_and_a_reserved_hand_as_resuming():
    hands = [{"name": "hand-ACME-17", "ref": "ACME-17", "state": "stopped", "waiting_for": None, "resuming": True}]
    status = sample(items=[], slots={"used": 2, "cap": 2}, hands=hands)
    live = [CAPTAIN, session("a1b2", "ACME-15"), session("c3d4", "ACME-17", state="stopped", status=None)]
    built = board.build(status, fleet.Fleet(live), NOW, TODAY, brief.UNICODE)
    [working] = [section for section in built.sections if section.name == "working"]
    assert built.slots == ("2", "2")
    assert [row.ref for row in working.rows] == ["ACME-15", "ACME-17"]
    assert "resuming" in working.rows[1].state
