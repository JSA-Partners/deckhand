from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand.step import Refusal
from deckhand.steps.checks import Checks
from deckhand.steps.review import Review
from deckhand.store.fake import FakeStore
from tests.test_step_build import BRANCH, Shell, labeled, repo, state
from tests.test_step_review import TEAM, BranchGit, planned, raw, record_round

__all__ = ["repo", "state"]


class Commands:
    def __init__(self, failing: str | None = None, output: str = "") -> None:
        self.failing, self.output = failing, output
        self.ran: list[tuple[str, Path]] = []

    def __call__(self, command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        self.ran.append((command, cwd))
        code = 1 if command == self.failing else 0
        return subprocess.CompletedProcess(command, code, "", self.output if code else "")


def configured(repo: Path, *commands: str) -> None:
    listed = ", ".join(json.dumps(c) for c in commands)
    (repo / ".claude" / "deckhand.toml").write_text(f'repository_label = "widgets"\nchecks = [{listed}]\n')


def run(store: FakeStore, repo: Path, commands: Commands, head: str = "c2") -> None:
    Checks(store, Shell(head=head), repo, commands).apply(["s1", *TEAM])


def held(store: FakeStore):
    [record] = [r for r in store.records("s1") if r.kind == "checks"]
    return record


def test_checks_record_pins_head(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    configured(repo, "make lint", "make types")
    commands = Commands()
    run(store, repo, commands)
    assert commands.ran == [("make lint", repo / ".worktrees" / BRANCH), ("make types", repo / ".worktrees" / BRANCH)]
    record = held(store)
    assert record.version == "c2"
    assert record.data == {"head": "c2", "passed": True, "command": None, "tail": None}


def test_failing_check_is_recorded_then_refused_and_stops(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    configured(repo, "make lint", "make types")
    output = "\n".join(f"line {n}" for n in range(30)) + "\ntoken ghp_abc123secret\n"
    commands = Commands(failing="make lint", output=output)
    with pytest.raises(Refusal, match="make lint failed"):
        run(store, repo, commands)
    assert [c for c, _ in commands.ran] == ["make lint"]
    data = held(store).data
    assert (data["passed"], data["command"]) == (False, "make lint")
    tail = data["tail"].splitlines()
    assert len(tail) == 20 and tail[-1] == "token [redacted]" and "ghp_" not in data["tail"]


def test_empty_checks_record_a_pass_and_context_says_so(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    commands = Commands()
    capsys.readouterr()
    Checks(store, Shell(), repo, commands).context(["s1", *TEAM])
    assert "no checks are configured" in capsys.readouterr().out
    run(store, repo, commands)
    assert commands.ran == [] and held(store).data["passed"] is True


def test_failing_lint_blocks_branch_review(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    configured(repo, "make lint")
    with pytest.raises(Refusal, match="make lint failed"):
        run(store, repo, Commands(failing="make lint", output="E501"), "c1")
    step = Review(store, BranchGit("c1", {"base1...HEAD": "+one\n"}), repo)
    for call in (
        lambda: step.context(["s1", "--stage", "branch", *TEAM]),
        lambda: record_round(step, tmp_path, raw(), stage="branch", target="x"),
    ):
        before = list(store.writes)
        with pytest.raises(Refusal, match="no passing checks"):
            call()
        assert store.writes == before


def test_new_head_needs_new_checks(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    configured(repo, "make lint")
    run(store, repo, Commands(), "c1")
    first = BranchGit("c1", {"base1...HEAD": "+one\n"})
    record_round(Review(store, first, repo), tmp_path, raw(severity="P1"), stage="branch")
    moved = Review(store, BranchGit("c2", {"base1...HEAD": "+one\n+two\n", "c1..HEAD": "+two\n"}), repo)
    with pytest.raises(Refusal, match="no passing checks"):
        moved.context(["s1", "--stage", "branch", *TEAM])
    run(store, repo, Commands(), "c2")
    capsys.readouterr()
    moved.context(["s1", "--stage", "branch", *TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["round"] == 2


def test_checks_without_a_worktree_refuses(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    store.set_labels("s1", ["widgets"])
    commands = Commands()
    with pytest.raises(Refusal, match="no worktree"):
        run(store, repo, commands)
    assert commands.ran == [] and not [r for r in store.records("s1") if r.kind == "checks"]


def test_checks_in_another_repository_refuses(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    store.set_labels("s1", ["gadgets"])
    commands = Commands()
    with pytest.raises(Refusal, match="another repository"):
        run(store, repo, commands)
    assert commands.ran == []
