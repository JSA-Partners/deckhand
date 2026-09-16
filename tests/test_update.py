"""The update step: GitHub brings a pull request branch up to date with main."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import FIXTURES, run_deckhand

PR_URL = "https://github.com/acme/widgets/pull/1000"


def _story(tmp_path: Path) -> dict[str, str]:
    data = json.loads((FIXTURES / "issue-approved.json").read_text(encoding="utf-8"))
    entry = {"author": {"login": "claude"}, "createdAt": "2026-09-05T09:00:00Z", "body": f"Pull request: {PR_URL}"}
    data["comments"].append(entry)
    path = tmp_path / "pr.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path), "GH_PR_STATE": "OPEN"}


def test_context_names_the_pull_request(fake_gh, tmp_path):
    result = run_deckhand("update", "context", "248", env=_story(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"Pull request: {PR_URL}\n"


def test_apply_asks_github_to_update_the_branch(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("update", "apply", "248", env=_story(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["Updated from main; the checks run again"]
    assert "api -X PUT repos/acme/widgets/pulls/1000/update-branch" in gh_calls()


def test_apply_refuses_without_a_pull_request(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("update", "apply", "248")

    assert result.returncode == 1
    assert result.stderr == "deckhand update apply: #248 has no open pull request\n"
    assert not any("update-branch" in c for c in gh_calls())


def test_apply_says_what_to_do_on_a_conflict(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("update", "apply", "248", env={**_story(tmp_path), "GH_UPDATE_BRANCH_FAILS": "1"})

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand update apply: GitHub could not merge main into the branch; merge origin/main in the "
        "worktree, resolve, commit, log a Deviation, and finish again\n"
    )
