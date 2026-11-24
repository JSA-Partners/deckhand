from __future__ import annotations

import stat
from pathlib import Path

import pytest

from deckhand import fleet, status
from deckhand.fleet import Session
from deckhand.queue import Item
from deckhand.step import Refusal
from deckhand.store.state import LocalState

AT = "2026-11-20T10:00:00.000Z"
BUDGET = {"feature": "Alpha", "days_used": 4, "budget_days": 7, "planning_note": False}
STAGE = {"name": "plan review", "round": 2, "cap": 3, "since": "2026-11-20T09:00:00.000Z"}
ROW = {"ref": "ACME-1", "title": "Orders", "state": "In Progress", "owner": "Pat", "blocked_by": [], "stage": STAGE}
FACTS = {
    "feature": "Alpha",
    "budget": BUDGET,
    "slots": {"used": 1, "cap": 2},
    "order": [ROW],
    "release": False,
}


def question(qid: str, at: str = "2026-11-20T09:30:00.000Z") -> Item:
    next = [f"decide context question {qid} --story ACME-1"]
    return Item("question", 2, "ACME-1", "Orders", at, 0, ["ACME-1 Orders"], next)


def session(name: str, state: str = "working", waiting_for: str | None = None) -> Session:
    return Session(f"id-{name}", name, f"sid-{name}", "/w", "2026-11-20T08:00:00.000Z", state, "busy", waiting_for)


def snapshot(waiting: list[Item], sessions: list[Session] = ()) -> dict:
    return status.snapshot(AT, "ACME", FACTS, waiting, list(sessions))


def test_snapshot_shape():
    hand = session(fleet.hand_name("ACME-1"), "blocked", fleet.PERMISSION)
    spike = session(fleet.spike_name("0123456789ab"))
    found = snapshot([question("q1")], [hand, spike])
    assert found == {
        "shape": 1,
        "written_at": AT,
        "team": "ACME",
        "feature": "Alpha",
        "budget": BUDGET,
        "slots": {"used": 1, "cap": 2},
        "items": [
            {
                "key": status.item_key(question("q1")),
                "kind": "question",
                "ref": "ACME-1",
                "title": "Orders",
                "since": "2026-11-20T09:30:00.000Z",
                "next": ["decide context question q1 --story ACME-1"],
                "head": None,
                "lines": ["ACME-1 Orders"],
            }
        ],
        "stories": [ROW],
        "hands": [
            {
                "name": "hand-ACME-1",
                "ref": "ACME-1",
                "state": "blocked",
                "waiting_for": fleet.PERMISSION,
                "started_at": "2026-11-20T08:00:00.000Z",
            },
            {
                "name": "spike-0123456789ab",
                "ref": "0123456789ab",
                "state": "working",
                "waiting_for": None,
                "started_at": "2026-11-20T08:00:00.000Z",
            },
        ],
        "release": False,
        "stale_since": None,
        "person_unknown": False,
    }


def test_an_item_with_no_time_has_no_since():
    assert snapshot([question("q1", at="")])["items"][0]["since"] is None


def test_two_questions_on_one_story_have_two_keys():
    assert status.item_key(question("q1")) != status.item_key(question("q2"))


def test_the_snapshot_round_trips_through_sqlite(tmp_path: Path):
    state = tmp_path / "state"
    found = snapshot([question("q1")])
    status.write(state, found)
    assert status.read(state) == found
    with LocalState(state / "deckhand.db") as rows:
        assert rows.get(status.SNAPSHOT) == found
    assert not (state / "captain.json").exists()
    assert {p.name: stat.S_IMODE(p.stat().st_mode) for p in state.iterdir()} == {"deckhand.db": 0o600}


def test_a_snapshot_item_carries_its_lines():
    assert snapshot([question("q1")])["items"][0]["lines"] == ["ACME-1 Orders"]


def test_a_write_replaces_the_last_one(tmp_path: Path):
    status.write(tmp_path, snapshot([question("q1")]))
    status.write(tmp_path, snapshot([]))
    assert status.read(tmp_path)["items"] == []


def put(state: Path, body: object) -> None:
    with LocalState(state / "deckhand.db") as rows:
        rows.put(status.SNAPSHOT, body)


@pytest.mark.parametrize("content", [[], {"shape": 0}, {}])
def test_read_is_none_unless_a_current_shape(tmp_path: Path, content: object):
    put(tmp_path, content)
    assert status.read(tmp_path) is None


def test_read_is_none_without_a_snapshot(tmp_path: Path):
    assert status.read(tmp_path) is None


def test_read_is_none_when_lines_are_not_strings(tmp_path: Path):
    found = snapshot([question("q1")])
    found["items"][0]["lines"] = [1]
    status.write(tmp_path, found)
    assert status.read(tmp_path) is None


@pytest.mark.parametrize("content", [b"not a database, just some bytes" * 100, b"\x00"])
def test_read_is_none_when_the_database_is_unreadable(tmp_path: Path, content: bytes):
    (tmp_path / "deckhand.db").write_bytes(content)
    assert status.read(tmp_path) is None


def test_read_is_none_when_the_state_folder_is_a_file(tmp_path: Path):
    (tmp_path / "state").write_text("not a folder")
    assert status.read(tmp_path / "state") is None


def test_a_write_to_a_state_folder_that_is_a_file_refuses(tmp_path: Path):
    (tmp_path / "state").write_text("not a folder")
    with pytest.raises(Refusal):
        status.write(tmp_path / "state", snapshot([]))
