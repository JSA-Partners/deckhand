from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import FIXTURES, run_deckhand

STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
APPROVED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-approved.json")}
BRANCH = "feat/248-guest-users-see-only"
ONE_BLOCKER = json.dumps([{"number": 240, "title": "Grant store", "state": "open"}])
IN_PROGRESS = (
    "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTSSF_STATUS "
    "--single-select-option-id opt_inprogress"
)
NEXT = "Next: implement the plan with superpowers:subagent-driven-development, then /deckhand:finish 248."
DRIFT = [
    "  internal/server/handlers/collections/list.go  missing",
    "  internal/store/collection.go:40-80  missing",
    "  internal/store/collection_test.go  missing",
]


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


def _branches(path: Path) -> list[str]:
    return _git(path, "branch", "--format=%(refname:short)").split()


def _writes(gh_calls) -> list[str]:
    return [call for call in gh_calls() if "item-edit" in call or "item-add" in call]


def _fields_without(tmp_path: Path, *names: str) -> dict[str, str]:
    """The field values fixture with `names` removed, so `get_field` answers None for them."""
    data = json.loads((FIXTURES / "graphql-fieldvalues.json").read_text(encoding="utf-8"))
    values = data["data"]["node"]["fieldValues"]
    values["nodes"] = [node for node in values["nodes"] if (node.get("field") or {}).get("name") not in names]
    path = tmp_path / f"fieldvalues-no-{'-'.join(names).lower()}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_FIELDVALUES_FILE": str(path)}


@pytest.fixture
def origin(repo: Path, tmp_path: Path) -> Path:
    """A bare origin holding main, so the story branch has somewhere to be pushed."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return bare


def _start(verb: str, repo: Path, env: dict[str, str] | None = None):
    return run_deckhand("start", verb, "248", cwd=repo, env={**APPROVED, **(env or {})})


# --- apply ------------------------------------------------------------------


def test_apply_refuses_a_stub(fake_gh, gh_calls, repo, origin):
    result = run_deckhand("start", "apply", "57", cwd=repo, env=STUB)

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: #57 is a stub; run /deckhand:new 57 first\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []
    assert _branches(repo) == ["main"]


def test_apply_refuses_with_an_open_blocker(fake_gh, gh_calls, repo, origin):
    result = _start("apply", repo, {"GH_BLOCKED_BY": ONE_BLOCKER})

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: blocked by #240 Grant store\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []
    assert _branches(repo) == ["main"]


def test_apply_refuses_when_not_on_the_board(fake_gh, gh_calls, repo, origin, tmp_path):
    result = _start("apply", repo, _fields_without(tmp_path, "Status"))

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: #248 is not on the board; run /deckhand:ready 248\n"
    assert _writes(gh_calls) == []
    assert _branches(repo) == ["main"]


def test_apply_refuses_without_a_kind(fake_gh, gh_calls, repo, origin, tmp_path):
    result = _start("apply", repo, _fields_without(tmp_path, "Kind"))

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: #248 has no Kind; run /deckhand:ready 248\n"
    assert _writes(gh_calls) == []
    assert _branches(repo) == ["main"]


def test_apply_creates_pushes_and_sets_in_progress(fake_gh, gh_calls, repo, origin):
    result = _start("apply", repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:3] == [
        f"Branch {BRANCH} created from origin/main",
        "Pushed",
        "Status=In Progress",
    ]
    assert _git(repo, "symbolic-ref", "HEAD").strip() == f"refs/heads/{BRANCH}"
    assert _branches(origin) == [BRANCH, "main"]
    assert [call for call in gh_calls() if "item-edit" in call] == [IN_PROGRESS]


def test_apply_on_an_existing_branch_leaves_status_alone(fake_gh, gh_calls, repo, origin):
    _git(repo, "push", "-q", "origin", f"main:{BRANCH}")

    result = _start("apply", repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"Existing branch {BRANCH}; status unchanged"
    assert _git(repo, "symbolic-ref", "HEAD").strip() == f"refs/heads/{BRANCH}"
    assert _writes(gh_calls) == []


def test_apply_fast_forwards_a_local_branch_that_has_no_upstream(fake_gh, repo, origin):
    _git(repo, "checkout", "-q", "-b", BRANCH)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "feat: store method")
    _git(repo, "push", "-q", "origin", BRANCH)  # no -u, so the branch tracks nothing
    tip = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "reset", "-q", "--hard", "HEAD~1")
    _git(repo, "checkout", "-q", "main")

    result = _start("apply", repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"Existing branch {BRANCH}; status unchanged"
    assert _git(repo, "rev-parse", "HEAD").strip() == tip


def test_apply_finishes_an_interrupted_start(fake_gh, gh_calls, repo, origin, tmp_path):
    _git(repo, "remote", "set-url", "--push", "origin", str(tmp_path / "nowhere.git"))
    assert _start("apply", repo).returncode == 1
    _git(repo, "remote", "set-url", "--push", "origin", str(origin))

    result = _start("apply", repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:3] == [
        f"Branch {BRANCH} is local only; finishing the interrupted start",
        "Pushed",
        "Status=In Progress",
    ]
    assert _branches(origin) == [BRANCH, "main"]
    assert [call for call in gh_calls() if "item-edit" in call] == [IN_PROGRESS]


def test_apply_prints_plan_commits_and_drift(fake_gh, repo, origin):
    _git(repo, "checkout", "-q", "-b", BRANCH)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "feat: store method")
    _git(repo, "push", "-q", "origin", BRANCH)
    _git(repo, "checkout", "-q", "main")
    sha = _git(repo, "rev-parse", "--short", BRANCH).strip()

    result = _start("apply", repo)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("## Plan") + 1] == "# Guest Collections Implementation Plan"
    assert lines[lines.index("## Commits") : lines.index("## Plan drift")] == [
        "## Commits",
        f"  {sha} feat: store method",
    ]
    assert lines[lines.index("## Plan drift") :] == ["## Plan drift", *DRIFT, NEXT]


def test_apply_does_not_set_status_when_the_push_fails(fake_gh, gh_calls, repo, origin, tmp_path):
    _git(repo, "remote", "set-url", "--push", "origin", str(tmp_path / "nowhere.git"))

    result = _start("apply", repo)

    assert result.returncode == 1
    assert result.stdout.splitlines() == [f"Branch {BRANCH} created from origin/main"]
    assert result.stderr.startswith("deckhand start: ")
    assert _writes(gh_calls) == []


# --- context ----------------------------------------------------------------


def test_context_prints_branch_blockers_plan_commits_and_drift(fake_gh, gh_calls, repo, origin):
    result = _start("context", repo, {"GH_BLOCKED_BY": ONE_BLOCKER})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:3] == [f"Branch: {BRANCH} (none)", "Blockers:", "  240  Grant store"]
    assert lines[lines.index("## Commits") : lines.index("## Plan drift")] == ["## Commits", "  none"]
    assert lines[lines.index("## Plan drift") :] == ["## Plan drift", *DRIFT]
    assert _branches(repo) == ["main"]
    assert _writes(gh_calls) == []


def test_context_reads_the_commits_in_a_clone_that_has_no_local_main(fake_gh, repo, origin, tmp_path):
    _git(repo, "checkout", "-q", "-b", BRANCH)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "feat: store method")
    _git(repo, "push", "-q", "origin", BRANCH)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--branch", BRANCH, str(origin), str(clone)], check=True)
    assert _branches(clone) == [BRANCH]
    sha = _git(clone, "rev-parse", "--short", "HEAD").strip()

    result = _start("context", clone)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("## Commits") : lines.index("## Plan drift")] == [
        "## Commits",
        f"  {sha} feat: store method",
    ]


def test_context_reports_the_branch_it_already_pushed(fake_gh, repo, origin):
    _git(repo, "push", "-q", "origin", f"main:{BRANCH}")

    result = _start("context", repo)

    assert result.stdout.splitlines()[0] == f"Branch: {BRANCH} (origin)"


def test_context_says_the_location_is_unknown_when_git_cannot_answer(fake_gh, repo):
    # No origin at all: the name is known, where the branch lives is not.
    result = _start("context", repo)

    assert result.returncode == 0, result.stderr
    line = result.stdout.splitlines()[0]
    assert line.startswith(f"Branch: {BRANCH} (unknown: ") and "origin" in line


def test_context_never_fails(fake_gh, repo, tmp_path):
    # A PATH holding only python3: bin/deckhand still starts, and neither git nor gh is on it.
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    (bare / "python3").symlink_to(sys.executable)

    result = run_deckhand("start", "context", "248", cwd=repo, env={"PATH": str(bare)})

    assert result.returncode == 0
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert lines[0].startswith("Branch: unavailable (")
    assert [line for line in lines if not line.startswith("  ")] == [
        lines[0],
        "Blockers:",
        "## Plan",
        "## Commits",
        "## Plan drift",
    ]
