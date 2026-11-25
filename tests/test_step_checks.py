from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from deckhand.step import Refusal
from deckhand.steps.checks import Checks, run_command
from deckhand.steps.review import Review
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import reapprove
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
    assert record.data == {
        "head": "c2",
        "passed": True,
        "command": None,
        "tail": None,
        "commands": ["make lint", "make types"],
    }


def test_checks_record_the_commands_that_ran(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    configured(repo, "make lint")
    run(store, repo, Commands())
    assert held(store).data["commands"] == ["make lint"]


def test_failing_check_is_recorded_then_refused_and_stops(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    configured(repo, "make lint", "make types")
    output = "\n".join(f"line {n}" for n in range(30)) + "\ntoken ghp_abc123secret\n"
    commands = Commands(failing="make lint", output=output)
    with pytest.raises(
        Refusal, match=r"make lint failed at \S+; the failure is recorded\. Fix it and run checks again"
    ):
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
    capsys.readouterr()
    step.context(["s1", "--stage", "branch", *TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["stop"] == "checks"
    before = list(store.writes)
    with pytest.raises(Refusal, match="no passing checks"):
        record_round(step, tmp_path, raw(), stage="branch", target="x")
    assert store.writes == before


def test_new_head_needs_new_checks(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    configured(repo, "make lint")
    run(store, repo, Commands(), "c1")
    first = BranchGit("c1", {"base1...HEAD": "+one\n"})
    record_round(Review(store, first, repo), tmp_path, raw(severity="P1"), stage="branch")
    moved = Review(store, BranchGit("c2", {"base1...HEAD": "+one\n+two\n", "c1..HEAD": "+two\n"}), repo)
    capsys.readouterr()
    moved.context(["s1", "--stage", "branch", *TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["stop"] == "checks"
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
    reapprove(store)
    commands = Commands()
    with pytest.raises(Refusal, match="another repository"):
        run(store, repo, commands)
    assert commands.ran == []


def test_dirty_worktree_refuses_and_records_nothing(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    configured(repo, "make lint")
    commands = Commands()
    before = list(store.writes)
    with pytest.raises(Refusal, match="uncommitted"):
        Checks(store, Shell(dirty=True), repo, commands).apply(["s1", *TEAM])
    assert commands.ran == [] and store.writes == before


def test_check_commands_run_on_the_trusted_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp = tmp_path / "temp"
    temp.mkdir()
    (temp / "git").write_text("#!/bin/sh\necho planted\n")
    (temp / "git").chmod(0o755)
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("PATH", f"{temp}{os.pathsep}{os.environ['PATH']}")
    done = run_command("git --version", tmp_path)
    assert done.stdout.startswith("git version")


def test_a_failing_check_at_the_signed_head_voids_the_sign_off(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    from deckhand import signoff
    from tests.test_step_signoff import ready, sign, voids

    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")
    configured(repo, "make test")
    with pytest.raises(Refusal, match="make test failed"):
        run(store, repo, Commands(failing="make test"))
    [void] = voids(store)
    assert void.data["reason"] == "checks failed: make test"
    run(store, repo, Commands())
    assert not signoff.current(store.records("s1"), "s1", "c2")
