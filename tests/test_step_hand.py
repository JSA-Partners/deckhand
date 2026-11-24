from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand import answers, asks, config, features, fleet, git, implplan, launch, queue, signoff, spikes
from deckhand.policy import Policy
from deckhand.records import HAND, Record
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.steps.hand import Hand
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import P1, approve_everything, setup
from tests.test_feature_approval import decided as decide_kind
from tests.test_implplan import manifest
from tests.test_step_stories import setup as empty_feature

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
    store.put_record(story, Record(asks.QUESTION, f"{story}:q-{nonce}", "v1", HAND, {"question": "Which queue?"}))
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
    assert settings["env"]["DECKHAND_HAND_KIND"] == "story"
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


def answer_the_stop(store: FakeStore, state: Path, answer: str, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    Decide(store).context(["stalled", "ACME-1", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    asked = shown["tool_input"]["questions"][0]
    answers.record(
        {"session_id": "sess", "tool_response": {"questions": [asked], "answers": {asked["question"]: answer}}}, state
    )
    Decide(store).apply([shown["nonce"], *TEAM])


def stalled_items(store: FakeStore, shell: Shell) -> list[queue.Item]:
    feature = features.open_feature(store, "ACME")
    found = queue.items(store, "ACME", feature, ME, fleet.sessions(shell), lambda story: Policy("widgets"))
    return [i for i in found if i.kind == queue.STALLED]


def test_restart_makes_the_story_due_and_clears_the_item(clone: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell()
    store.assign("s2", "someone-else")
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    shell.rows = [row("ACME-1", "failed")]
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1"]
    assert [i.ref for i in stalled_items(store, shell)] == ["ACME-1"]
    answer_the_stop(store, state, "Restart", capsys)
    assert stalled_items(store, shell) == []
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1", "hand-ACME-1"]
    assert [i.ref for i in stalled_items(store, shell)] == ["ACME-1"]


def test_leave_it_stopped_clears_the_item_and_starts_nothing(
    clone: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store, shell = building(), Shell()
    store.assign("s2", "someone-else")
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    shell.rows = [row("ACME-1", "failed")]
    answer_the_stop(store, state, "Leave it stopped", capsys)
    assert stalled_items(store, shell) == []
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1"]


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
    assert json.loads(capsys.readouterr().out)["startable"] == []


def test_wake_once_per_decision(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell()
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    shell.rows = [row("ACME-1", "blocked", "waiting")]
    capsys.readouterr()
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "nothing to wake"
    decided(store, "s1", "n1")
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    first = capsys.readouterr().out.strip()
    assert first.startswith("send to hand-ACME-1: ")
    [wake] = [r for r in store.records("s1") if r.kind == asks.WAKE]
    assert wake.version == "n1"
    written = list(store.writes)
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "nothing to wake"
    assert store.writes == written
    decided(store, "s1", "n2")
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    second = capsys.readouterr().out.strip()
    assert second.startswith("send to hand-ACME-1: ")
    assert second != first and "n1" in first and "n2" in second
    [wake] = [r for r in store.records("s1") if r.kind == asks.WAKE]
    assert wake.version == "n2"
    assert shell.launched() == ["hand-ACME-1"]


def test_fill_starts_no_hand_for_a_merged_story_whose_last_wake_followed_the_sign_off(clone: Path):
    store, shell = building(), Shell()
    store.assign("s2", "someone-else")
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    decided(store, "s1", "n1")
    shell.rows = [row("ACME-1", "stopped")]
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    store.put_record("s1", Record(signoff.KIND, "s1", "h1", "person:session-1", {}))
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    store.set_issue_state("s1", "Done")
    assert asks.wake_due(store.records("s1"), "s1") is None
    shell.rows = [row("ACME-1", "done")]
    launched = list(shell.launched())
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == launched


def test_fill_never_wakes_a_merged_story_for_a_decision_made_before_the_merge(clone: Path):
    store, shell = building(), Shell()
    store.assign("s2", "someone-else")
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    store.put_record("s1", Record(signoff.KIND, "s1", "h1", "person:session-1", {"nonce": "n1"}))
    store.set_issue_state("s1", "Done")
    shell.rows = [row("ACME-1", "done")]
    launched = list(shell.launched())
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == launched
    assert not [r for r in store.records("s1") if r.kind == asks.WAKE]


def test_fill_restarts_a_merged_story_whose_hand_left_its_chores_open(clone: Path):
    store, shell = building(), Shell()
    store.assign("s2", "someone-else")
    chores(store, "s1", "drop the old table")
    hand(store, shell).apply(["fill", *TEAM])
    store.put_record("s1", Record(signoff.KIND, "s1", "h1", "person:session-1", {"nonce": "n1"}))
    store.set_issue_state("s1", "Done")
    shell.rows = [row("ACME-1", "done")]
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1", "hand-ACME-1"]


class StopFails(Shell):
    def __call__(self, argv, cwd=None, stdin=None):
        if argv[:2] == ["claude", "stop"]:
            raise git.GitError("claude stop", 124, "timed out")
        return super().__call__(argv, cwd, stdin)


def test_a_stop_that_fails_still_resumes_the_hand(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), StopFails()
    store.assign("s2", "someone-else")
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    decided(store, "s1", "n1")
    shell.rows = [row("ACME-1", "stopped")]
    capsys.readouterr()
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "resumed hand-ACME-1"
    assert shell.launched() == ["hand-ACME-1", "hand-ACME-1"]


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
    assert asks.wake_due(store.records("s1"), "s1") is not None
    shell.rows = [row("ACME-1", "stopped"), row("ACME-2", "done")]
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "resumed hand-ACME-1"
    argv, cwd = shell.launches()[-1]
    assert argv[argv.index("--resume") + 1] == "sid-ACME-1"
    assert cwd == clone / ".worktrees" / "acme-1-orders"
    called = [argv for argv, _ in shell.calls]
    assert called.index(["claude", "stop", "id-ACME-1"]) == called.index(argv) - 1
    records = store.records("s1")
    assert asks.wake_due(records, "s1") is None
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


def asked(store: FakeStore, story: str) -> None:
    question = {"question": "Which queue?", "options": ["Redis", "SQS"], "why": "Both fit."}
    store.put_record(story, asks.question_record(story, asks.parse_question(json.dumps(question))))


def test_wake_live_waiting_hand_waits_for_a_slot(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell()
    store.assign("s2", ME)
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    asked(store, "s1")
    shell.rows = [row("ACME-1", "blocked", "waiting")]
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1", "hand-ACME-2"]
    shell.rows = [row("ACME-1", "blocked", "waiting"), row("ACME-2", "working", "busy")]
    decided(store, "s1", "n1")
    written = list(store.writes)
    capsys.readouterr()
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "waits for a slot"
    assert store.writes == written
    assert asks.wake_due(store.records("s1"), "s1") is not None
    shell.rows = [row("ACME-1", "blocked", "waiting"), row("ACME-2", "done")]
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.startswith("send to hand-ACME-1: ")


def test_wake_refuses_a_story_now_owned_by_another(clone: Path):
    store, shell = building(), Shell()
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    decided(store, "s1", "n1")
    shell.rows = [row("ACME-1", "blocked", "waiting")]
    store.assign("s1", "someone-else")
    written = list(store.writes)
    with pytest.raises(Refusal, match="ACME-1 belongs to someone-else"):
        hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert store.writes == written


def test_context_omits_a_started_story_waiting_on_the_person(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell()
    store.assign("s2", "someone-else")
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    asked(store, "s1")
    shell.rows = [row("ACME-1", "done")]
    capsys.readouterr()
    hand(store, shell).context([*TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["startable"] == []


class NoKey:
    def viewer(self) -> dict:
        raise Refusal("missing credentials file: personal-key")


def test_context_prints_without_a_personal_key(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = building()
    Hand(store, Shell(row("ACME-1", "working", "busy")), person=NoKey()).context(TEAM)
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == ["missing credentials file: personal-key", "slots: 1 of 1"]
    assert "live: hand-ACME-1 working" in lines


QUESTION = "Does the queue keep order?"


def planning() -> FakeStore:
    store = setup()
    decide_kind(store, "contract", P1, "Approve", ["s1", "s2"])
    return store


def question_file(tmp_path: Path, text: str = QUESTION) -> str:
    path = tmp_path / "question.md"
    path.write_text(text)
    return str(path)


def spike_row(key: str, state: str, status: str | None = None) -> dict:
    return row("x", state, status) | {"name": f"spike-{key}"}


def test_spike_starts_a_detached_hand_with_its_own_key(clone: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = empty_feature(), Shell()
    hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    key = spikes.key(P1, QUESTION)
    assert spikes.question_path(answers.state_dir(), key).read_text() == QUESTION
    worktree = clone / ".worktrees" / f"spike-{key}"
    [add] = [a for a, _ in shell.calls if a[:3] == ["git", "worktree", "add"]]
    assert add == ["git", "worktree", "add", "--detach", str(worktree), "origin/HEAD"]
    [(argv, cwd)] = shell.launches()
    settings = settings_of(argv)
    assert argv == launch.hand_argv(f"spike-{key}", settings, f"/deckhand:spike {key}", None, None)
    assert cwd == worktree
    assert settings["env"]["DECKHAND_ROLE"] == "hand"
    assert settings["env"]["DECKHAND_SPIKE"] == key
    assert "DECKHAND_STORY" not in settings["env"]
    protected = launch.protected(config.config_dir(), answers.state_dir())
    story_settings = launch.hand_settings(key, clone, worktree, Policy.load(clone), config.local(), protected)
    story_settings["env"].pop("DECKHAND_STORY")
    story_settings["env"]["DECKHAND_SPIKE"] = key
    story_settings["env"]["DECKHAND_HAND_KIND"] = "spike"
    assert settings == story_settings
    assert settings["sandbox"]["enabled"] is True
    assert "AskUserQuestion" in settings["permissions"]["deny"]
    assert settings["sandbox"]["filesystem"]["denyWrite"]
    assert capsys.readouterr().out.strip() == f"started spike-{key}"


def test_spike_refused_once_stories_exist(clone: Path, tmp_path: Path):
    store, shell = planning(), Shell()
    written = list(store.writes)
    with pytest.raises(Refusal, match="already has stories"):
        hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    assert store.writes == written
    assert shell.launches() == []
    assert not (answers.state_dir() / "spikes").exists()


def test_spike_refused_after_approval(clone: Path, tmp_path: Path):
    store, shell = building(), Shell()
    with pytest.raises(Refusal, match="already has stories"):
        hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    assert shell.launches() == []


def test_spike_holds_a_slot(clone: Path, tmp_path: Path):
    store = empty_feature()
    key = spikes.key(P1, QUESTION)
    shell = Shell(spike_row("0123456789ab", "working", "busy"))
    with pytest.raises(Refusal, match="no slot is free: 1 of 1 in use"):
        hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    assert shell.launches() == []
    assert not spikes.question_path(answers.state_dir(), key).exists()


def test_a_live_spike_leaves_no_slot_for_a_hand(clone: Path):
    store, shell = building(), Shell(spike_row("0123456789ab", "working", "busy"))
    with pytest.raises(Refusal, match="no slot is free: 1 of 1 in use"):
        hand(store, shell).apply(["start", "ACME-1", *TEAM])
    assert shell.launches() == []


def test_spike_already_live_starts_nothing(clone: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    store = empty_feature()
    key = spikes.key(P1, QUESTION)
    shell = Shell(spike_row(key, "working", "busy"))
    hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    assert shell.launches() == []
    assert capsys.readouterr().out.strip() == f"spike-{key} is already live"


def test_spike_takes_a_question_file_with_one_trailing_newline(clone: Path, tmp_path: Path):
    store, shell = empty_feature(), Shell()
    hand(store, shell).apply(
        ["spike", "--repository", "widgets", "--file", question_file(tmp_path, QUESTION + "\n"), *TEAM]
    )
    key = spikes.key(P1, QUESTION)
    assert spikes.question_path(answers.state_dir(), key).read_text() == QUESTION
    assert spikes.keys(answers.state_dir(), P1) == {key}


def test_spike_refused_by_policy_writes_nothing(clone: Path, tmp_path: Path):
    (clone / ".claude" / "deckhand.toml").write_text('repository_label = "widgets"\nbogus = 1\n')
    store, shell = empty_feature(), Shell()
    with pytest.raises(Refusal, match="unknown key bogus"):
        hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    assert shell.launches() == []
    assert [a for a, _ in shell.calls if a[:3] == ["git", "worktree", "add"]] == []
    assert not (answers.state_dir() / "spikes").exists()
    assert not (clone / ".worktrees").exists()


def test_context_names_a_live_spike(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = empty_feature(), Shell(spike_row("0123456789ab", "working", "busy"))
    hand(store, shell).context(TEAM)
    assert capsys.readouterr().out.splitlines()[:2] == ["slots: 1 of 1", "live: spike-0123456789ab working"]


@pytest.mark.parametrize("text", ["one\ntwo", "x" * 201, "  padded", "", "\n", "a\tb", "a\rb", "q\n\n"])
def test_spike_refuses_a_question_a_title_cannot_hold(clone: Path, tmp_path: Path, text: str):
    store, shell = empty_feature(), Shell()
    with pytest.raises(Refusal, match="one line of at most 200 characters"):
        hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path, text), *TEAM])
    assert shell.launches() == []
    assert not (answers.state_dir() / "spikes").exists()


def test_spike_refuses_a_repository_without_a_clone(clone: Path, tmp_path: Path):
    store, shell = empty_feature(), Shell()
    with pytest.raises(Refusal, match="no clone for gadgets"):
        hand(store, shell).apply(["spike", "--repository", "gadgets", "--file", question_file(tmp_path), *TEAM])
    assert shell.launches() == []


def test_fill_starts_drafts_never_spikes(clone: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    configure(clone, 3)
    store, shell = planning(), Shell()
    asked_key = spikes.key(P1, QUESTION)
    path = spikes.question_path(answers.state_dir(), asked_key)
    path.parent.mkdir(parents=True)
    path.write_text(QUESTION)
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1", "hand-ACME-2"]
    prompts = [argv[-1] for argv, _ in shell.launches()]
    assert prompts == ["/deckhand:draft ACME-1", "/deckhand:draft ACME-2"]
    [start] = starts(store, "s1")
    assert start.data == {"kind": "draft", "session": "hand-ACME-1"}
    assert capsys.readouterr().out.splitlines()[:2] == ["started hand-ACME-1", "started hand-ACME-2"]


def test_fill_waits_for_the_contract(clone: Path):
    store, shell = setup(), Shell()
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launches() == []


def test_fill_skips_a_drafted_story(clone: Path):
    configure(clone, 3)
    store, shell = planning(), Shell()
    store.put_record("s1", Record(queue.DRAFT, "s1", "v1", HAND, {}))
    store.put_record("s2", Record(queue.TAKEN, "s2", "v1", HAND, {}))
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launches() == []


def test_start_picks_the_draft_prompt_while_planning(clone: Path):
    store, shell = planning(), Shell()
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    [(argv, cwd)] = shell.launches()
    assert argv[-1] == "/deckhand:draft ACME-1"
    assert settings_of(argv)["env"]["DECKHAND_HAND_KIND"] == "draft"
    assert cwd == clone / ".worktrees" / "acme-1-orders"
    assert ("put_record", "s1", "worktree", "s1") in [w for w in store.writes if w[0] == "put_record"]


def drafted(store: FakeStore, story: str) -> None:
    start = {"kind": "draft", "session": f"hand-{store.issue(story)['identifier']}"}
    store.put_record(story, Record(asks.HAND_START, story, "", HAND, start))
    store.put_record(story, Record(queue.DRAFT, story, "d1", "agent:draft", {}))
    store.put_record(story, Record(queue.TAKEN, story, "d1", "person:x", {}))


def test_fill_starts_a_fresh_build_hand_for_a_drafted_story(clone: Path):
    configure(clone, 3)
    store = building()
    drafted(store, "s1")
    shell = Shell(row("ACME-1", "done"))
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1", "hand-ACME-2"]
    argv, _ = shell.launches()[0]
    assert argv[-1] == PROMPT.format("ACME-1")
    assert "--resume" not in argv
    [start] = starts(store, "s1")
    assert start.data["kind"] == "story"


def test_a_feature_finding_decision_wakes_no_draft_hand(clone: Path, capsys: pytest.CaptureFixture[str]):
    configure(clone, 3)
    store = planning()
    drafted(store, "s1")
    data = {"answer": "Decline", "nonce": "n1", "by": ME}
    store.put_record("s1", Record("finding", "f-1", "v1", "person:session-1", data))
    shell = Shell(row("ACME-1", "done"))
    capsys.readouterr()
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "nothing to wake"
    assert shell.launches() == []


@pytest.mark.parametrize("phase", ["drafted while planning", "building"])
def test_a_wake_relaunches_only_the_hand_the_story_still_needs(
    clone: Path, capsys: pytest.CaptureFixture[str], phase: str
):
    store = planning() if phase.endswith("planning") else building()
    store.put_record("s1", Record(asks.HAND_START, "s1", "", HAND, {"kind": "draft", "session": "hand-ACME-1"}))
    if phase.endswith("planning"):
        store.put_record("s1", Record(queue.DRAFT, "s1", "d1", "agent:draft", {}))
    decided(store, "s1", "n1")
    shell = Shell(row("ACME-1", "done"))
    capsys.readouterr()
    hand(store, shell).apply(["wake", "ACME-1", *TEAM])
    assert capsys.readouterr().out.strip() == "nothing to wake"
    assert shell.launches() == []


def test_fill_resumes_a_woken_hand_before_new_work(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell()
    store.assign("s2", ME)
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    decided(store, "s1", "n1")
    shell.rows = [row("ACME-1", "stopped")]
    capsys.readouterr()
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1", "hand-ACME-1"]
    assert capsys.readouterr().out.splitlines() == ["resumed hand-ACME-1", "no slot is free: 1 of 1 in use"]
    [wake] = [r for r in store.records("s1") if r.kind == asks.WAKE]
    assert wake.version == "n1"
    assert asks.wake_due(store.records("s1"), "s1") is None


def test_fill_names_a_live_woken_hand_to_send_to(clone: Path, capsys: pytest.CaptureFixture[str]):
    configure(clone, 2)
    store, shell = building(), Shell()
    store.assign("s2", ME)
    hand(store, shell).apply(["start", "ACME-1", *TEAM])
    decided(store, "s1", "n1")
    shell.rows = [row("ACME-1", "blocked", "waiting")]
    capsys.readouterr()
    hand(store, shell).apply(["fill", *TEAM])
    assert shell.launched() == ["hand-ACME-1", "hand-ACME-2"]
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("send to hand-ACME-1: ")
    assert out[1:] == ["started hand-ACME-2", "no slot is free: 2 of 2 in use"]
    assert asks.wake_due(store.records("s1"), "s1") is None
