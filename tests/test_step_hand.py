from __future__ import annotations

from pathlib import Path

import pytest

from deckhand import answers, asks, config, features, fleet, launch, spikes, startable
from deckhand.policy import Policy
from deckhand.records import HAND, Record
from deckhand.step import Refusal
from deckhand.steps.hand import Hand
from deckhand.store.fake import FakeStore
from deckhand.store.local import default_path
from deckhand.store.state import HandRow, LocalState
from tests.shell import Shell, row
from tests.test_feature_approval import P1, approve_everything, setup
from tests.test_feature_approval import decided as decide_kind
from tests.test_queue import started
from tests.test_step_stories import setup as empty_feature

ME = "viewer-1"
TEAM = ["--team-key", "ACME"]


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


def building() -> FakeStore:
    store = setup()
    approve_everything(store)
    return store


def hand(store: FakeStore, shell: Shell) -> Hand:
    return Hand(store, shell)


def decided(store: FakeStore, story: str, nonce: str) -> None:
    store.put_record(story, Record(asks.QUESTION, f"{story}:q-{nonce}", "v1", HAND, {"question": "Which queue?"}))
    data = {"answer": "Redis", "nonce": nonce, "by": ME}
    store.put_record(story, Record(asks.ANSWER, f"q-{nonce}", "v1", "person:session-1", data))


def test_spike_on_an_idle_but_alive_session_starts_nothing(
    clone: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    store = empty_feature()
    key = spikes.key(P1, QUESTION)
    shell = Shell(spike_row(key, "idle", "idle"))
    hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    assert shell.launches() == []
    assert wanted() == {}
    assert capsys.readouterr().out.strip() == f"spike-{key} is already live"


def test_an_idle_hand_on_its_own_prompt_is_never_due(clone: Path):
    store, shell = building(), Shell()
    store.assign("s1", ME)
    store.assign("s2", "someone-else")
    started(store, "s1")
    shell.rows = [row("ACME-1", "idle", "idle") | {"waitingFor": "input needed"}]
    feature = features.open_feature(store, "ACME")
    hands = fleet.hands(fleet.sessions(shell).sessions)
    clones = config.local().clones
    [work] = [w for w in startable.startable(store, feature, ME, clones) if w.story["identifier"] == "ACME-1"]
    assert not startable.due(work, feature, hands, lambda _: Policy.load(clone), 0)
    shell.rows = [row("ACME-1", "failed")]
    assert startable.due(work, feature, fleet.hands(fleet.sessions(shell).sessions), lambda _: Policy.load(clone), 0)


def test_context_shows_live_hands(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = building(), Shell(row("ACME-1", "working", "busy"))
    hand(store, shell).context(TEAM)
    assert capsys.readouterr().out.splitlines() == ["live: hand-ACME-1 working"]
    hand(store, Shell(fleet_fails=True)).context(TEAM)
    assert "the fleet read failed" in capsys.readouterr().out


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


def wanted() -> dict[str, HandRow]:
    with LocalState(default_path()) as rows:
        return rows.hands()


def test_spike_writes_a_wanted_row_and_launches_nothing(clone: Path, tmp_path: Path, capsys):
    store, shell = empty_feature(), Shell()
    hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    key = spikes.key(P1, QUESTION)
    name = f"spike-{key}"
    assert spikes.question_path(answers.state_dir(), key).read_text() == QUESTION
    [row_] = wanted().values()
    worktree = str(clone / ".worktrees" / name)
    assert (row_.name, row_.ref, row_.issue, row_.kind, row_.cwd) == (name, key, "", launch.SPIKE, worktree)
    assert shell.launches() == []
    assert [a for a, _ in shell.calls if a[:2] == ["git", "worktree"]] == []
    assert capsys.readouterr().out.strip() == "asked: the engine starts it"


@pytest.mark.parametrize("args", [["fill"], ["start", "ACME-1"], ["wake", "ACME-1"]])
def test_hand_apply_refuses_fill_start_and_wake(clone: Path, args: list[str]):
    store, shell = building(), Shell()
    written = list(store.writes)
    with pytest.raises(Refusal, match="apply takes spike"):
        hand(store, shell).apply([*args, *TEAM])
    assert store.writes == written
    assert shell.calls == []
    assert wanted() == {}


def test_spike_refused_once_stories_exist(clone: Path, tmp_path: Path):
    store, shell = planning(), Shell()
    written = list(store.writes)
    with pytest.raises(Refusal, match="already has stories"):
        hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    assert store.writes == written
    assert shell.launches() == []
    assert wanted() == {}
    assert not (answers.state_dir() / "spikes").exists()


def test_spike_refused_after_approval(clone: Path, tmp_path: Path):
    store, shell = building(), Shell()
    with pytest.raises(Refusal, match="already has stories"):
        hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    assert shell.launches() == []
    assert wanted() == {}


def test_spike_holds_a_slot(clone: Path, tmp_path: Path):
    store = empty_feature()
    key = spikes.key(P1, QUESTION)
    shell = Shell(spike_row("0123456789ab", "working", "busy"))
    with pytest.raises(Refusal, match="no slot is free: 1 of 1 in use"):
        hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    assert shell.launches() == []
    assert wanted() == {}
    assert not spikes.question_path(answers.state_dir(), key).exists()


def test_spike_already_live_starts_nothing(clone: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    store = empty_feature()
    key = spikes.key(P1, QUESTION)
    shell = Shell(spike_row(key, "working", "busy"))
    hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path), *TEAM])
    assert shell.launches() == []
    assert wanted() == {}
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
    assert wanted() == {}
    assert [a for a, _ in shell.calls if a[:3] == ["git", "worktree", "add"]] == []
    assert not (answers.state_dir() / "spikes").exists()
    assert not (clone / ".worktrees").exists()


def test_context_names_a_live_spike(clone: Path, capsys: pytest.CaptureFixture[str]):
    store, shell = empty_feature(), Shell(spike_row("0123456789ab", "working", "busy"))
    hand(store, shell).context(TEAM)
    assert capsys.readouterr().out.splitlines()[:1] == ["live: spike-0123456789ab working"]


@pytest.mark.parametrize("text", ["one\ntwo", "x" * 201, "  padded", "", "\n", "a\tb", "a\rb", "q\n\n"])
def test_spike_refuses_a_question_a_title_cannot_hold(clone: Path, tmp_path: Path, text: str):
    store, shell = empty_feature(), Shell()
    with pytest.raises(Refusal, match="one line of at most 200 characters"):
        hand(store, shell).apply(["spike", "--repository", "widgets", "--file", question_file(tmp_path, text), *TEAM])
    assert shell.launches() == []
    assert wanted() == {}
    assert not (answers.state_dir() / "spikes").exists()


def test_spike_refuses_a_repository_without_a_clone(clone: Path, tmp_path: Path):
    store, shell = empty_feature(), Shell()
    with pytest.raises(Refusal, match="no clone for gadgets"):
        hand(store, shell).apply(["spike", "--repository", "gadgets", "--file", question_file(tmp_path), *TEAM])
    assert shell.launches() == []
    assert wanted() == {}
