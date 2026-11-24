from __future__ import annotations

import json
import subprocess

from deckhand import fleet
from deckhand.fleet import Session
from deckhand.git import GitError
from deckhand.records import iso

ROWS = [
    {
        "pid": 100,
        "cwd": "/work/widgets",
        "kind": "interactive",
        "startedAt": 1795000000000,
        "sessionId": "00000000-0000-4000-8000-000000000001",
        "name": "captain",
        "status": "busy",
    },
    {
        "pid": 101,
        "cwd": "/work/widgets",
        "kind": "interactive",
        "startedAt": 1795000001000,
        "sessionId": "00000000-0000-4000-8000-000000000002",
        "name": "widgets-2e",
        "status": "waiting",
        "waitingFor": "input needed",
    },
    {
        "id": "aaaa0001",
        "cwd": "/work/widgets/.worktrees/acme-1-orders",
        "kind": "background",
        "startedAt": 1795000002000,
        "sessionId": "aaaa0001-0000-4000-8000-000000000003",
        "name": "hand-ACME-1",
        "state": "failed",
    },
    {
        "id": "aaaa0002",
        "pid": 102,
        "cwd": "/work/widgets/.worktrees/acme-1-orders",
        "kind": "background",
        "startedAt": 1795000003000,
        "sessionId": "aaaa0002-0000-4000-8000-000000000004",
        "name": "hand-ACME-1",
        "state": "working",
        "status": "busy",
    },
    {
        "id": "aaaa0003",
        "pid": 103,
        "cwd": "/work/widgets/.worktrees/acme-2-receipts",
        "kind": "background",
        "startedAt": 1795000004000,
        "sessionId": "aaaa0003-0000-4000-8000-000000000005",
        "name": "hand-ACME-2",
        "state": "blocked",
        "status": "waiting",
        "waitingFor": "permission prompt",
    },
    {
        "id": "aaaa0004",
        "pid": 104,
        "cwd": "/work/widgets/.worktrees/spike-0123456789ab",
        "kind": "background",
        "startedAt": 1795000005000,
        "sessionId": "aaaa0004-0000-4000-8000-000000000006",
        "name": "spike-0123456789ab",
        "state": "working",
        "status": "busy",
    },
]


class Agents:
    def __init__(self, stdout: str = json.dumps(ROWS), code: int = 0, error: bool = False) -> None:
        self.stdout, self.code, self.error = stdout, code, error
        self.argvs: list[list[str]] = []

    def __call__(self, argv, cwd=None, stdin=None):
        self.argvs.append(argv)
        if self.error:
            raise GitError("claude agents", -1, "claude not found")
        return subprocess.CompletedProcess(argv, self.code, self.stdout, "it broke")


def session(name: str, state: str, status: str | None, waiting_for: str | None = None, at: int = 0) -> fleet.Session:
    return fleet.Session(f"id-{name}", name, f"sid-{name}", "/w", iso(at), state, status, waiting_for)


def test_sessions_read_every_field_the_queue_uses():
    runner = Agents()
    read = fleet.sessions(runner)
    assert runner.argvs == [["claude", "agents", "--json", "--all"]]
    assert read.note == ""
    hand = read.sessions[3]
    assert hand == fleet.Session(
        "aaaa0002",
        "hand-ACME-1",
        "aaaa0002-0000-4000-8000-000000000004",
        "/work/widgets/.worktrees/acme-1-orders",
        "2026-11-18T11:06:43.000Z",
        "working",
        "busy",
        None,
    )
    assert (read.sessions[0].id, read.sessions[0].state) == ("", "")
    assert read.sessions[2].status is None


def test_newest_session_wins_for_a_name():
    hands = fleet.hands(fleet.sessions(Agents()).sessions)
    assert sorted(hands) == ["ACME-1", "ACME-2"]
    assert (hands["ACME-1"].id, hands["ACME-1"].state) == ("aaaa0002", "working")
    reversed_rows = fleet.hands(list(reversed(fleet.sessions(Agents()).sessions)))
    assert reversed_rows["ACME-1"].id == "aaaa0002"
    assert list(fleet.spikes(fleet.sessions(Agents()).sessions)) == ["0123456789ab"]
    assert (fleet.hand_name("ACME-1"), fleet.spike_name("0123456789ab")) == ("hand-ACME-1", "spike-0123456789ab")


def test_agents_failure_is_empty_fleet():
    for runner in (Agents(code=1), Agents(stdout="not json"), Agents(stdout='{"a": 1}'), Agents(error=True)):
        read = fleet.sessions(runner)
        assert read.sessions == []
        assert read.note.startswith("the fleet read failed")


def test_waiting_hand_frees_its_slot():
    assert fleet.slots_used(fleet.sessions(Agents()).sessions) == 3
    held = [
        session("hand-ACME-1", "working", "busy"),
        session("hand-ACME-2", "blocked", "waiting", "permission prompt"),
        session("hand-ACME-3", "blocked", "waiting", "input needed"),
        session("hand-ACME-4", "done", None),
        session("hand-ACME-5", "failed", None),
        session("spike-0123456789ab", "working", "busy"),
        session("widgets-2e", "working", "busy"),
    ]
    assert fleet.slots_used(held) == 3
    assert fleet.slots_used(held + [session("hand-ACME-1", "done", None, at=1)]) == 3


def test_only_working_or_permission_blocked_hands_hold_a_slot():
    assert fleet.holds_slot(session("hand-A", "working", "busy"))
    assert fleet.holds_slot(session("hand-A", "blocked", "waiting", "permission prompt"))
    assert not fleet.holds_slot(session("hand-A", "blocked", "waiting", "input needed"))
    assert not fleet.holds_slot(session("hand-A", "working", None))
    assert not fleet.holds_slot(session("hand-A", "failed", None))


def test_live_needs_a_status_and_a_working_state():
    assert fleet.live(session("hand-A", "working", "busy"))
    assert fleet.live(session("hand-A", "blocked", "waiting"))
    assert not fleet.live(session("hand-A", "working", None))
    assert not fleet.live(session("hand-A", "done", None))


def test_alive_is_any_status_and_live_needs_a_working_state():
    assert fleet.alive(session("hand-A", "idle", "idle", "input needed"))
    assert fleet.alive(session("hand-A", "blocked", "waiting"))
    assert not fleet.alive(session("hand-A", "failed", None))
    assert not fleet.live(session("hand-A", "idle", "idle", "input needed"))
    assert fleet.live(session("hand-A", "blocked", "waiting"))


def test_an_alive_session_wins_over_a_newer_stopped_one():
    working = Session("a1", "hand-ACME-1", "sid-a", "/w", "2026-11-18T10:00:00.000Z", "working", "busy", None)
    stopped = Session("a2", "hand-ACME-1", "sid-b", "/w", "2026-11-18T11:00:00.000Z", "stopped", None, None)
    assert fleet.hands([working, stopped])["ACME-1"].id == "a1"
    assert fleet.slots_used([working, stopped]) == 1
