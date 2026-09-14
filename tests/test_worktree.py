"""Tests for deckhand.worktree: where a branch is checked out, making a worktree, removing one, the sweep."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from deckhand import git, worktree
from tests.conftest import FIXTURES, run_git

BRANCH = "feat/248-guest-users-see-only"


def _add(repo, branch: str, *args: str):
    """A worktree for `branch` at the module's path, set as a scene by git itself, not the module."""
    path = worktree.path_for(branch)
    run_git(repo, "worktree", "add", str(path), *(args or (branch,)))
    return path


# --- the facts ---------------------------------------------------------------


def test_the_clone_root_is_the_same_from_a_linked_worktree(repo, monkeypatch):
    other = _add(repo, BRANCH, "-b", BRANCH)

    assert worktree.clone_root() == repo.resolve()
    assert worktree.from_clone()
    monkeypatch.chdir(other)
    assert worktree.clone_root() == repo.resolve()
    assert not worktree.from_clone()


def test_the_path_is_the_branch_under_the_clones_claude_worktrees(repo):
    assert worktree.path_for(BRANCH) == repo.resolve() / ".claude" / "worktrees" / BRANCH


def test_a_branch_is_checked_out_here_elsewhere_or_nowhere(repo, monkeypatch):
    assert worktree.checked_out(BRANCH) is None  # no branch at all
    run_git(repo, "branch", BRANCH)
    assert worktree.checked_out(BRANCH) is None  # a branch in no worktree
    other = _add(repo, BRANCH)
    assert worktree.checked_out(BRANCH) == other
    assert not worktree.here(other)
    assert worktree.here(worktree.checked_out("main"))
    monkeypatch.chdir(other)
    assert worktree.here(worktree.checked_out(BRANCH))
    assert not worktree.here(worktree.checked_out("main"))


def test_a_worktree_deleted_by_hand_is_nowhere(repo):
    other = _add(repo, BRANCH, "-b", BRANCH)
    shutil.rmtree(other)

    assert worktree.checked_out(BRANCH) is None
    assert "prunable" not in run_git(repo, "worktree", "list", "--porcelain")


@pytest.fixture
def origin(repo, tmp_path):
    """A bare origin holding main, so there is an origin/main to branch from."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    run_git(repo, "remote", "add", "origin", str(bare))
    run_git(repo, "push", "-q", "-u", "origin", "main")
    return bare


def _exclude(repo) -> str:
    path = repo / ".git" / "info" / "exclude"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- add ----------------------------------------------------------------------


def test_add_cuts_a_new_branch_from_origin_main_in_its_worktree(repo, origin):
    path = worktree.add(BRANCH, new=True)

    assert path == worktree.path_for(BRANCH)
    assert run_git(path, "symbolic-ref", "HEAD").strip() == f"refs/heads/{BRANCH}"
    assert run_git(path, "rev-parse", "HEAD") == run_git(repo, "rev-parse", "origin/main")
    assert run_git(repo, "symbolic-ref", "HEAD").strip() == "refs/heads/main"
    assert run_git(repo, "status", "--porcelain") == ""
    assert _exclude(repo).endswith(".claude/worktrees/\n")


def test_add_takes_an_existing_branch_checked_out_nowhere(repo):
    run_git(repo, "branch", BRANCH)

    path = worktree.add(BRANCH, new=False)

    assert worktree.checked_out(BRANCH) == path
    assert run_git(path, "rev-parse", "HEAD") == run_git(repo, "rev-parse", "main")


def test_the_exclude_line_is_written_once(repo, origin):
    worktree.add(BRANCH, new=True)
    worktree.add("fix/9-nine", new=True)

    assert _exclude(repo).count(".claude/worktrees/") == 1


def test_add_leaves_an_ignore_the_repository_already_has_alone(repo, origin):
    (repo / ".gitignore").write_text(".claude/worktrees/\n", encoding="utf-8")
    run_git(repo, "add", ".gitignore")
    run_git(repo, "commit", "-qm", "chore: ignore worktrees")

    worktree.add(BRANCH, new=True)

    assert ".claude/worktrees/" not in _exclude(repo)
    assert run_git(repo, "status", "--porcelain") == ""


def test_add_fails_in_gits_words_when_the_branch_exists(repo, origin):
    run_git(repo, "branch", BRANCH)

    with pytest.raises(git.GitError, match="already exists"):
        worktree.add(BRANCH, new=True)


REPO = "acme/widgets"


def _story(tmp_path, number: int, state: str) -> dict[str, str]:
    """The issue fixture as story `number` in `state`, for the fake gh's per-number switch."""
    data = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
    data.update(number=number, state=state)
    path = tmp_path / f"issue-{number}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {f"GH_ISSUE_FILE_{number}": str(path)}


def _branches(repo) -> list[str]:
    return run_git(repo, "branch", "--format=%(refname:short)").split()


# --- remove ------------------------------------------------------------------


def test_remove_takes_the_worktree_the_branch_and_the_tracking_ref(repo, origin):
    path = worktree.add(BRANCH, new=True)
    run_git(path, "push", "-q", "-u", "origin", BRANCH)

    worktree.remove(BRANCH, path)

    assert not path.exists()
    assert _branches(repo) == ["main"]
    assert run_git(repo, "branch", "-r", "--format=%(refname:short)").split() == ["origin/main"]


def test_remove_refuses_a_worktree_with_uncommitted_changes(repo, origin):
    path = worktree.add(BRANCH, new=True)
    (path / "wip").write_text("x\n", encoding="utf-8")

    with pytest.raises(git.GitError, match="modified or untracked"):
        worktree.remove(BRANCH, path)

    assert path.exists()
    assert worktree.checked_out(BRANCH) == path


# --- the sweep ----------------------------------------------------------------


def test_the_sweep_removes_closed_stories_and_leaves_open_ones(fake_gh, repo, origin, monkeypatch, tmp_path):
    closed = worktree.add("fix/57-closed", new=True)
    opened = worktree.add("feat/9-open", new=True)
    for name, value in {**_story(tmp_path, 57, "CLOSED"), **_story(tmp_path, 9, "OPEN")}.items():
        monkeypatch.setenv(name, value)

    lines = worktree.sweep(REPO)

    assert lines == [f"Removed worktree {closed}"]
    assert not closed.exists()
    assert opened.exists()
    assert _branches(repo) == ["feat/9-open", "main"]


def test_the_sweep_skips_the_story_it_is_told_to(fake_gh, repo, origin, monkeypatch, tmp_path):
    closed = worktree.add("fix/57-closed", new=True)
    for name, value in _story(tmp_path, 57, "CLOSED").items():
        monkeypatch.setenv(name, value)

    assert worktree.sweep(REPO, exclude=57) == []
    assert closed.exists()


def test_the_sweep_runs_only_from_the_clone(fake_gh, repo, origin, monkeypatch, tmp_path):
    closed = worktree.add("fix/57-closed", new=True)
    for name, value in _story(tmp_path, 57, "CLOSED").items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(closed)

    assert worktree.sweep(REPO) == []
    assert closed.exists()


def test_the_sweep_ignores_worktrees_that_are_not_a_story(no_real_gh, repo, origin, tmp_path):
    """A worktree on another branch, or a story branch outside .claude/worktrees, is nobody's to remove."""
    scratch = _add(repo, "scratch", "-b", "scratch")
    elsewhere = tmp_path / "elsewhere"
    run_git(repo, "worktree", "add", str(elsewhere), "-b", "fix/57-closed")

    assert worktree.sweep(REPO) == []
    assert scratch.exists()
    assert elsewhere.exists()


def test_the_sweep_leaves_a_worktree_it_cannot_remove_and_says_so(fake_gh, repo, origin, monkeypatch, tmp_path):
    closed = worktree.add("fix/57-closed", new=True)
    (closed / "wip").write_text("x\n", encoding="utf-8")
    for name, value in _story(tmp_path, 57, "CLOSED").items():
        monkeypatch.setenv(name, value)

    (line,) = worktree.sweep(REPO)

    assert line.startswith(f"Left worktree {closed}: ")
    assert "modified or untracked" in line
    assert closed.exists()


def test_the_sweep_is_nothing_outside_a_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert worktree.sweep(REPO) == []


def test_the_sweep_leaves_a_story_it_cannot_read_and_says_so(fake_gh, repo, origin, monkeypatch):
    unread = worktree.add("fix/57-closed", new=True)
    monkeypatch.setenv("GH_ISSUE_VIEW_FAILS", "57")

    (line,) = worktree.sweep(REPO)

    assert line.startswith(f"Left worktree {unread}: ")
    assert unread.exists()
