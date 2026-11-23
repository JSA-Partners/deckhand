from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from deckhand import fleet, status
from deckhand.fleet import Session
from deckhand.queue import Item

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


def snapshot(waiting: list[Item], sessions: list[Session] = (), previous: dict | None = None) -> dict:
    return status.snapshot(AT, "ACME", FACTS, waiting, list(sessions), previous)


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
        "notified": [],
    }


def test_an_item_with_no_time_has_no_since():
    assert snapshot([question("q1", at="")])["items"][0]["since"] is None


def test_two_questions_on_one_story_have_two_keys():
    assert status.item_key(question("q1")) != status.item_key(question("q2"))


def test_notified_keeps_the_keys_of_items_still_waiting():
    kept, gone = status.item_key(question("q1")), status.item_key(question("q2"))
    found = snapshot([question("q1")], previous={"notified": [kept, gone]})
    assert found["notified"] == [kept]


def test_written_file_reads_back(tmp_path: Path):
    state = tmp_path / "state"
    found = snapshot([question("q1")])
    status.write(state, found)
    assert status.read(state) == found
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE((state / status.FILE).stat().st_mode) == 0o600
    assert [p.name for p in state.iterdir()] == [status.FILE]


def test_a_write_replaces_the_last_one(tmp_path: Path):
    status.write(tmp_path, snapshot([question("q1")]))
    status.write(tmp_path, snapshot([]))
    assert status.read(tmp_path)["items"] == []


def test_a_failed_write_leaves_the_last_file_and_no_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    status.write(tmp_path, snapshot([question("q1")]))

    def refuse(*_):
        raise OSError("disk full")

    monkeypatch.setattr(status.os, "replace", refuse)
    with pytest.raises(OSError):
        status.write(tmp_path, snapshot([]))
    assert len(status.read(tmp_path)["items"]) == 1
    assert [p.name for p in tmp_path.iterdir()] == [status.FILE]


@pytest.mark.parametrize(
    "content",
    [None, b"", b"{not json", b"\xff\xfe", b"[]", json.dumps({"shape": 0}).encode(), json.dumps({}).encode()],
)
def test_read_is_none_unless_a_current_shape(tmp_path: Path, content: bytes | None):
    if content is not None:
        (tmp_path / status.FILE).write_bytes(content)
    assert status.read(tmp_path) is None


@pytest.mark.parametrize("notified", [None, [["a"]], [1], "abc"])
def test_read_is_none_when_notified_is_not_a_list_of_strings(tmp_path: Path, notified: object):
    status.write(tmp_path, snapshot([]) | {"notified": notified})
    assert status.read(tmp_path) is None


def test_read_is_none_without_notified(tmp_path: Path):
    found = snapshot([])
    del found["notified"]
    status.write(tmp_path, found)
    assert status.read(tmp_path) is None


def test_read_is_none_when_unreadable(tmp_path: Path):
    (tmp_path / status.FILE).mkdir()
    assert status.read(tmp_path) is None
