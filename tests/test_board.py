from __future__ import annotations

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
    return {"name": name, "round": round, "cap": 3 if round else None, "since": at(minutes) if minutes else None}


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
        "notified": [],
    }
    return found | changes


def session(id: str, ref: str, state: str = "working", status: str | None = "busy", **extra) -> fleet.Session:
    fields = {"cwd": "/w", "started_at": at(30), "waiting_for": None} | extra
    return fleet.Session(id, f"hand-{ref}", f"sid-{id}", state=state, status=status, **fields)


LIVE = [
    session("a1b2", "ACME-15"),
    session("c3d4", "ACME-17", status="waiting"),
    session("e5f6", "ACME-18", state="stopped", status=None),
]


def shown(status: dict | None = None, sessions: list | None = LIVE, cols: int = 100, rows: int | None = None, **kw):
    found = board.build(sample() if status is None else status, sessions, NOW, TODAY)
    return frame.render(found, G, cols, rows, **kw)


def texts(lines: list[frame.Line]) -> list[str]:
    return [line.text.rstrip() for line in lines]


def section(lines: list[frame.Line], name: str) -> list[str]:
    found = texts(lines)
    start = next(i for i, text in enumerate(found) if text.startswith(f"-- {name}  "))
    rest = found[start + 1 :]
    return rest[: next((i for i, text in enumerate(rest) if not text.startswith(" ")), len(rest))]


def test_sections_come_in_order_with_their_counts():
    rules = [text for text in texts(shown()) if text.startswith("-- ")]
    assert [rule.split("  ")[0] + "  " + rule.split("  ")[1].split()[0] for rule in rules] == [
        "-- needs you  2",
        "-- working  2",
        "-- queued  2",
        "-- done today  1",
    ]


def test_needs_you_lists_the_oldest_first_with_kind_and_wait():
    rows = section(shown(), "needs you")
    assert rows[0].split() == ["!", "ACME-14", "Print", "every", "greeting", "sign-off", "14m"]
    assert rows[1].split() == ["!", "ACME-16", "Rotate", "the", "access", "log", "question", "3m"]
    assert rows[2] == "   type next in the captain to take them in turn"


def test_working_rows_show_the_stage_and_the_hands_own_status():
    rows = section(shown(), "working")
    assert rows[0].split()[:2] == ["*", "ACME-15"]
    assert rows[0].split()[-4:] == ["branch", "review", "2/3", "6m"]
    assert rows[1].split()[-3:] == ["building,", "waiting", "1m"]


def test_queued_rows_have_no_hand_and_no_item():
    rows = section(shown(), "queued")
    assert [row.split()[1] for row in rows] == ["ACME-18", "ACME-19"]
    assert rows[0].endswith("waits for a slot")
    assert rows[1].endswith("blocked by ACME-18")


def test_a_free_slot_reads_ready():
    status = sample(slots={"used": 0, "cap": 3})
    rows = section(shown(status, sessions=[]), "queued")
    assert rows[-2].endswith("ready")


def test_done_today_counts_only_merges_since_midnight():
    rows = section(shown(), "done today")
    assert len(rows) == 1
    assert rows[0].split() == ["+", "ACME-13", "Add", "a", "farewell", "line", "merged", "2h"]


def test_empty_sections_other_than_needs_you_are_left_out():
    status = sample(stories=[story("ACME-14", "Print every greeting"), story("ACME-20", "Later")], hands=[])
    rules = [text.split("  ")[0] for text in texts(shown(status, sessions=[])) if text.startswith("-- ")]
    assert rules == ["-- needs you", "-- queued"]


def test_without_a_live_read_the_file_hands_are_working_and_no_attach_shows():
    lines = shown(sessions=None, selected="story:ACME-15", expanded="story:ACME-15")
    rows = section(lines, "working")
    assert [row.split()[1] for row in rows[:1]] == ["ACME-15"]
    assert not any("claude attach" in text for text in texts(lines))


def test_enter_shows_the_next_command_and_the_attach_command():
    lines = shown(selected="item:k14", expanded="item:k14")
    rows = section(lines, "needs you")
    assert rows[1].split() == ["next:", "signoff", "context", "--story", "ACME-14"]
    lines = shown(selected="story:ACME-15", expanded="story:ACME-15")
    assert section(lines, "working")[1].split() == ["hand:", "claude", "attach", "a1b2"]
    assert [line.selected for line in lines if "ACME-15" in line.text] == [True]


def test_the_header_says_how_old_the_file_is():
    status = sample(slots={"used": 1, "cap": 2})
    assert texts(shown(status, sessions=None))[0] == " Captain  Alpha | day 2 of 7 | slots 1/2 | updated 6s ago"
    assert texts(shown(status))[0].endswith("slots 2/2 | updated 6s ago")


def test_an_old_file_gets_a_stale_banner():
    assert not texts(shown())[1].strip()
    banner = texts(shown(sample(written_at=at(45))))[1]
    assert banner == " ! stale: the captain last read Linear 45m ago; run /deckhand:captain to refresh"


def test_an_unreachable_linear_gets_a_stale_banner():
    banner = texts(shown(sample(written_at=at(5), stale_since=at(1))))[1]
    assert banner == " ! stale: Linear was unreachable, this is the state from 5m ago; run /deckhand:captain to retry"


def test_no_status_file_teaches_how_to_start():
    found = board.build(None, LIVE, NOW, TODAY)
    lines = texts(frame.render(found, G, 100))
    assert lines[0] == " Captain"
    assert "   no captain has run yet; start one with /deckhand:captain" in lines


def test_nothing_waiting_says_so_with_the_working_count():
    rows = section(shown(sample(items=[])), "needs you")
    assert rows == ["   nothing needs you; 2 working"]


def test_hand_and_file_text_is_cleaned():
    hostile = "Orders\x1b]0;owned\x07\nnext"
    status = sample(items=[], stories=[story("ACME-15", hostile, stage=stage("building", 1))])
    sessions = [session("a1", "ACME-15", status="waiting\x1b[2J", waiting_for="you\r\x1b[31m")]
    lines = shown(status, sessions, selected="story:ACME-15", expanded="story:ACME-15")
    joined = "\n".join(line.text for line in lines)
    assert "\x1b" not in joined and "\x07" not in joined and "\r" not in joined
    assert "Orders ]0;owned next" in joined


def test_a_malformed_file_still_renders():
    status = sample(items=[{"kind": 3}], stories=[{"ref": None, "stage": {"round": 2}}, "junk"], budget={"x": 1})
    lines = texts(shown(status))
    assert lines[0].startswith(" Captain  Alpha")


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
