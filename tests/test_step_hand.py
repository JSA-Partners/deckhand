from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand import asks, config, implplan, launch, signoff
from deckhand.records import HAND, Record
from deckhand.step import Refusal
from deckhand.steps.hand import Hand
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import approve_everything, setup
from tests.test_implplan import manifest

ME = "viewer-1"
TEAM = ["--team-key", "ACME"]
PROMPT = "/deckhand:story {}"


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    path = tmp_path / "widgets"
    (path / ".claude").mkdir(parents=True)
    (path / ".claude" / "deckhand.toml").write_text('repository_label = "widgets"\n')
    configure(path, 1)
    return path


def configure(clone: Path, slot_cap: int) -> None:
    directory = config.config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    text = f'slot_cap = {slot_cap}\n\n[clones]\nwidgets = "{clone}"\n'
    (directory / config.FILE).write_text(text)


def row(ref: str, state: str, status: str | None = None, at: int = 1795000000000) -> dict:
    found = {"id": f"id-{ref}", "name": f"hand-{ref}", "sessionId": f"sid-{ref}", "cwd": "/w", "startedAt": at}
    return found | {"state": state} | ({"status": status} if status else {})


class Shell:
    def __init__(self, *rows: dict, fleet_fails: bool = False) -> None:
        self.rows = list(rows)
        self.fleet_fails = fleet_fails
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, argv, cwd=None, stdin=None):
        self.calls.append((argv, cwd))
        out, code = "", 0
        if argv[:2] == ["claude", "agents"]:
            out, code = json.dumps(self.rows), 1 if self.fleet_fails else 0
        elif argv[:2] == ["git", "show-ref"]:
            code = 1
        return subprocess.CompletedProcess(argv, code, out, "")

    def launches(self) -> list[tuple[list[str], Path | None]]:
        return [(argv, cwd) for argv, cwd in self.calls if argv[:2] == ["claude", "--bg"]]

    def launched(self) -> list[str]:
        return [argv[argv.index("--name") + 1] for argv, _ in self.launches()]


def building() -> FakeStore:
    store = setup()
    approve_everything(store)
    return store


def hand(store: FakeStore, shell: Shell) -> Hand:
    return Hand(store, shell, person=store)


def starts(store: FakeStore, story: str) -> list[Record]:
    return [r for r in store.records(story) if r.kind == asks.HAND_START]


def settings_of(argv: list[str]) -> dict:
    return json.loads(argv[argv.index("--settings") + 1])


def decided(store: FakeStore, story: str, nonce: str) -> None:
    data = {"answer": "Redis", "nonce": nonce, "by": ME}
    store.put_record(story, Record(asks.ANSWER, f"q-{nonce}", "v1", "person:session-1", data))


def test_start_runs_bg_in_worktree(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell()
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    [(argv, cwd)] = shell.launches()
    worktree = clone / ".worktrees" / "acme-1-orders"
    settings = settings_of(argv)
    assert argv == launch.hand_argv("hand-ACME-1", settings, PROMPT.format("ACME-1"), None, None)
    assert cwd == worktree
    assert (settings["env"]["DECKHAND_ROLE"], settings["env"]["DECKHAND_STORY"]) == ("hand", "ACME-1")
    [add] = [a for a, _ in shell.calls if a[:3] == ["git", "worktree", "add"]]
    assert str(worktree) in add
    assert store.issue("s1")["assignee"] == ME
    [start] = starts(store, "s1")
    assert start.data == {"kind": "story", "session": "hand-ACME-1"}
    puts = [w for w in store.writes if w[0] == "put_record"]
    assert puts[-1] == ("put_record", "s1", asks.HAND_START, "s1")
    assert ("put_record", "s1", "worktree", "s1") in puts
    assert capsys.readouterr().out.strip() == "started hand-ACME-1"


def test_start_refuses_without_free_slot(clone: Path):
    store, shell = building(), Shell(row("ACME-2", "working", "busy"))
    written = list(store.writes)
    with pytest.raises(Refusal, match="no slot is free: 1 of 1 in use"):
        hand(store, shell).apply(["start", "ACME-1", *TEAM])
    assert store.writes == written
    assert shell.launches() == []


def test_start_refuses_blocked_story(clone: Path):
    store, shell = building(), Shell()
    store.relate("s1", "s2")
    written = list(store.writes)
    with pytest.raises(Refusal, match="ACME-2 waits on ACME-1, which is not Done"):
        hand(store, shell).apply(["start", "ACME-2", *TEAM])
    assert store.writes == written
    store.assign("s1", "someone-else")
    written = list(store.writes)
    with pytest.raises(Refusal, match="ACME-1 belongs to someone-else"):
        hand(store, shell).apply(["start", "ACME-1", *TEAM])
    assert store.writes == written
    assert shell.launches() == []


def test_start_with_live_hand_starts_nothing(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell(row("ACME-1", "working", "busy"))
    written = list(store.writes)
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    assert store.writes == written
    assert shell.launches() == []
    assert capsys.readouterr().out.strip() == "hand-ACME-1 is already live"


def test_apply_refuses_when_the_fleet_read_failed(clone: Path):
    store, shell = building(), Shell(fleet_fails=True)
    store.put_record("s1", Record(asks.HAND_START, "s1", "", HAND, {"kind": "story", "session": "hand-ACME-1"}))
    decided(store, "s1", "n1")
    written = list(store.writes)
    for args in (["start", "ACME-1"], ["fill"], ["wake", "ACME-1"]):
        with pytest.raises(Refusal, match="the fleet read failed"):
            hand(store, shell).apply([*args, *TEAM])
    assert store.writes == written
    assert shell.launches() == []


def test_fill_follows_build_order(clone: Path, capsys: pytest.CaptureFixture[str]):
    configure(clone, 2)
    store, shell = building(), Shell()
    store.relate("s2", "s1")
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-2"]
    shell.rows = [row("ACME-2", "working", "busy")]
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-2"]
    store.set_issue_state("s2", "Done")
    shell.rows = [row("ACME-2", "done")]
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-2", "hand-ACME-1"]
    assert "started hand-ACME-1" in capsys.readouterr().out


def test_fill_stops_at_the_slot_cap(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell()
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1"]
    shell.rows = [row("ACME-1", "working", "busy")]
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1"]
    assert capsys.readouterr().out.splitlines()[-1] == "no slot is free: 1 of 1 in use"


def test_fill_restarts_stalled_once_per_record(clone: Path):
    store, shell = building(), Shell()
    store.assign("s2", "someone-else")
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    shell.rows = [row("ACME-1", "failed")]
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1"]
    checked = Record("checks", "s1", "c2", "agent:checks", {"head": "c2", "passed": False})
    store.put_record("s1", checked)
    [checked] = [r for r in store.records("s1") if r.kind == "checks"]
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1", "hand-ACME-1"]
    argv, _ = shell.launches()[-1]
    assert argv[argv.index("--resume") + 1] == "sid-ACME-1"
    [start] = starts(store, "s1")
    assert start.at > checked.at
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1", "hand-ACME-1"]


def test_each_start_records_a_fresh_time(clone: Path):
    store, shell = building(), Shell()
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    [first] = starts(store, "s1")
    shell.rows = [row("ACME-1", "failed")]
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    [second] = starts(store, "s1")
    assert second.at > first.at


def chores(store: FakeStore, story: str, *texts: str) -> None:
    plan = Record(implplan.PURPOSE, story, "p1", "agent:implplan", manifest(after_merge=list(texts)))
    store.put_record(story, plan)


def test_after_merge_work_starts_after_done(clone: Path, capsys: pytest.CaptureFixture[str]):
    configure(clone, 3)
    store, shell = building(), Shell()
    chores(store, "s1", "drop the old table")
    chores(store, "s2", "rotate the secret")
    store.assign("s1", ME)
    store.assign("s2", ME)
    store.set_issue_state("s1", "Done")
    capsys.readouterr()
    hand(store, shell).context([*TEAM, "--json"])
    listed = json.loads(capsys.readouterr().out)["startable"]
    assert [(i["ref"], i["work"]) for i in listed] == [("ACME-2", "build"), ("ACME-1", "after-merge")]
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-2", "hand-ACME-1"]
    shell.rows = [row("ACME-1", "done"), row("ACME-2", "done")]
    store.set_issue_state("s2", "Done")
    store.put_record("s2", Record("checks", "s2", "c2", "agent:checks", {"head": "c2", "passed": True}))
    store.put_record("s1", signoff.after_record(signoff.AFTER_DONE, "s1", 1, "drop the old table", {"note": "x"}))
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-2", "hand-ACME-1", "hand-ACME-2"]
    capsys.readouterr()
    hand(store, shell).context([*TEAM, "--json"])
    assert [i["ref"] for i in json.loads(capsys.readouterr().out)["startable"]] == ["ACME-2"]


def test_wake_once_per_decision(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell()
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    shell.rows = [row("ACME-1", "blocked", "waiting")]
    capsys.readouterr()
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "nothing to wake"
    decided(store, "s1", "n1")
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    out = capsys.readouterr().out.strip()
    assert out.startswith("send to hand-ACME-1: ")
    [wake] = [r for r in store.records("s1") if r.kind == asks.WAKE]
    assert wake.version == "n1"
    written = list(store.writes)
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "nothing to wake"
    assert store.writes == written
    decided(store, "s1", "n2")
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.startswith("send to hand-ACME-1: ")
    [wake] = [r for r in store.records("s1") if r.kind == asks.WAKE]
    assert wake.version == "n2"
    assert shell.launched() == ["hand-ACME-1"]


def test_wake_gone_hand_resumes(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell()
    store.assign("s2", ME)
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    decided(store, "s1", "n1")
    shell.rows = [row("ACME-1", "stopped"), row("ACME-2", "working", "busy")]
    written = list(store.writes)
    capsys.readouterr()
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "waits for a slot"
    assert store.writes == written
    assert asks.wake_due(store.records("s1")) is not None
    shell.rows = [row("ACME-1", "stopped"), row("ACME-2", "done")]
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "resumed hand-ACME-1"
    argv, cwd = shell.launches()[-1]
    assert argv[argv.index("--resume") + 1] == "sid-ACME-1"
    assert cwd == clone / ".worktrees" / "acme-1-orders"
    records = store.records("s1")
    assert asks.wake_due(records) is None
    [wake] = [r for r in records if r.kind == asks.WAKE]
    [start] = starts(store, "s1")
    assert (wake.version, start.at > wake.at) == ("n1", True)


def test_context_shows_slots_live_hands_and_startable(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell(row("ACME-1", "working", "busy"))
    store.assign("s1", ME)
    hand(store, shell).context(TEAM)
    assert capsys.readouterr().out.splitlines() == [
        "slots: 1 of 1",
        "live: hand-ACME-1 working",
        "startable:",
        "- ACME-2 build",
    ]
    hand(store, Shell(fleet_fails=True)).context(TEAM)
    assert "the fleet read failed" in capsys.readouterr().out
