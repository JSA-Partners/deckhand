from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deckhand import git


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def origin(repo: Path, tmp_path: Path) -> Path:
    """A bare origin holding main, so the remote shapes are the ones a real push meets."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main")
    return bare


def test_run_returns_stdout_without_its_trailing_newline(repo):
    assert git.run("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "main"


def test_run_takes_the_working_directory_from_the_process_when_none_is_given(repo):
    assert git.run("rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_run_keeps_a_carriage_return_the_command_printed(repo):
    (repo / "w.txt").write_bytes(b"a\r\n")
    _git(repo, "add", "w.txt")

    assert git.run("diff", "--cached", cwd=repo).endswith("+a\r")


def test_run_folds_the_indented_detail_into_the_line_that_ends_with_a_colon(repo):
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "f").write_text("branch\n")
    _git(repo, "commit", "-qam", "chore: change f")
    _git(repo, "checkout", "-q", "main")
    (repo / "f").write_text("working\n")

    with pytest.raises(git.GitError) as raised:
        git.run("checkout", "other", cwd=repo)

    assert str(raised.value) == ("error: Your local changes to the following files would be overwritten by checkout: f")


def test_run_reports_the_fatal_line_past_the_progress_and_hints_printed_before_it(repo, origin):
    _git(repo, "commit", "-q", "--allow-empty", "-m", "chore: theirs")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "chore: mine")

    with pytest.raises(git.GitError) as raised:
        git.run("pull", "--ff-only", "origin", "main", cwd=repo)

    assert str(raised.value) == "fatal: Not possible to fast-forward, aborting."


def test_run_reports_the_first_of_several_fatal_lines_from_a_missing_remote(repo, tmp_path):
    nowhere = tmp_path / "nowhere.git"
    _git(repo, "remote", "add", "origin", str(nowhere))

    with pytest.raises(git.GitError) as raised:
        git.run("ls-remote", "--heads", "origin", "main", cwd=repo)

    assert str(raised.value) == f"fatal: '{nowhere}' does not appear to be a git repository"


def test_run_says_so_when_git_is_not_installed(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    with pytest.raises(git.GitError) as raised:
        git.run("status", cwd=repo)

    assert str(raised.value) == "git is not installed or not on PATH"


def test_message_prefers_the_rejected_line_over_the_push_summary():
    stderr = (
        "To /tmp/origin.git\n"
        " ! [rejected]        feat/248-guest -> feat/248-guest (stale info)\n"
        "error: failed to push some refs to '/tmp/origin.git'\n"
        "hint: Updates were rejected because the tip of your current branch is behind its remote.\n"
    )

    assert git.message(stderr) == "! [rejected] feat/248-guest -> feat/248-guest (stale info)"
