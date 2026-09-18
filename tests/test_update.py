"""The update step: a pull request or a still-building branch is brought up to date with main."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import FIXTURES, run_deckhand

PR_URL = "https://github.com/acme/widgets/pull/1000"
BRANCH = "feat/248-guest-users-see-only"


def _story(tmp_path: Path) -> dict[str, str]:
    data = json.loads((FIXTURES / "issue-approved.json").read_text(encoding="utf-8"))
    entry = {"author": {"login": "claude"}, "createdAt": "2026-09-05T09:00:00Z", "body": f"Pull request: {PR_URL}"}
    data["comments"].append(entry)
    path = tmp_path / "pr.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path), "GH_PR_STATE": "OPEN"}


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


def _sha(path: Path, ref: str) -> str:
    return _git(path, "rev-parse", ref).strip()


def _commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", message)


@pytest.fixture
def origin(repo: Path, tmp_path: Path) -> Path:
    """A bare origin holding main, so the story branch has somewhere to fetch it from."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return bare


@pytest.fixture
def branch(repo: Path, origin: Path) -> str:
    """The story branch, checked out in `repo`, one commit ahead of the main it branched from."""
    _git(repo, "checkout", "-q", "-b", BRANCH)
    _commit(repo, "story.py", "x = 1\n", "feat: story work")
    return BRANCH


def _building_story(tmp_path: Path, reviewed: str) -> dict[str, str]:
    """The approved story with a `Reviewed:` entry and no `Pull request:` entry: still building."""
    data = json.loads((FIXTURES / "issue-approved.json").read_text(encoding="utf-8"))
    entry = {"author": {"login": "claude"}, "createdAt": "2026-09-05T09:00:00Z", "body": f"Reviewed: {reviewed} clean"}
    data["comments"].append(entry)
    path = tmp_path / "building.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


def test_context_names_the_pull_request(fake_gh, tmp_path):
    result = run_deckhand("update", "context", "248", env=_story(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"Pull request: {PR_URL}\n"


def test_apply_asks_github_to_update_the_branch(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("update", "apply", "248", env=_story(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["Updated from main; the checks run again"]
    assert "api -X PUT repos/acme/widgets/pulls/1000/update-branch" in gh_calls()


def test_apply_refuses_without_a_pull_request_or_a_branch(fake_gh, gh_calls, repo):
    result = run_deckhand("update", "apply", "248", cwd=repo)

    assert result.returncode == 1
    assert result.stderr == "deckhand update apply: #248 has no open pull request and no branch checked out here\n"
    assert not any("update-branch" in c for c in gh_calls())


def test_apply_says_what_to_do_on_a_conflict(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("update", "apply", "248", env={**_story(tmp_path), "GH_UPDATE_BRANCH_FAILS": "1"})

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand update apply: GitHub could not merge main into the branch; merge origin/main in the "
        "worktree, resolve, commit, log a Deviation, and finish again\n"
    )


def test_apply_merges_main_when_the_branch_has_no_pull_request(fake_gh, gh_calls, repo, origin, branch):
    """Still building means no pull request yet; the merge happens locally instead of on GitHub."""
    reviewed = _sha(repo, "HEAD")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "other.py", "y = 2\n", "feat: other story")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", branch)

    result = run_deckhand("update", "apply", "248", cwd=repo, env=_building_story(repo.parent, reviewed))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"Merged origin/main into {branch}"]
    assert not any("update-branch" in c for c in gh_calls())
    assert "other story" in _git(repo, "log", "--oneline")


def test_apply_keeps_the_reviewed_commit_reachable_after_the_merge(fake_gh, gh_calls, repo, origin, branch):
    """finish depends on the reviewed commit staying an ancestor of HEAD; the merge must not move it."""
    reviewed = _sha(repo, "HEAD")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "other.py", "y = 2\n", "feat: other story")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", branch)

    result = run_deckhand("update", "apply", "248", cwd=repo, env=_building_story(repo.parent, reviewed))

    assert result.returncode == 0, result.stderr
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", reviewed, "HEAD"], cwd=repo, check=False)
    assert ancestor.returncode == 0
