from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import FIXTURES, fieldvalues, run_deckhand, run_git

STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
APPROVED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-approved.json")}
REVIEWED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-reviewed.json")}
REVIEW_DATE = "2026-09-02T09:00:00Z"  # the Review: entry in the reviewed fixture
BRANCH = "feat/248-guest-users-see-only"
ONE_BLOCKER = json.dumps([{"number": 240, "title": "Grant store", "state": "open"}])
IN_PROGRESS = (
    "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTSSF_STATUS "
    "--single-select-option-id opt_inprogress"
)
ASSIGN = "issue edit 248 --repo acme/widgets --add-assignee @me"
COMMENT = "issue comment 248 --repo acme/widgets"
STARTED = "Started: the plan holds against the code as of today."
LANDED = "## Landed on main since the review"
NO_REVIEW = [LANDED, "  no review entry to date from"]
# The plan's Create line is absent: a file the plan will write is not drift.
DRIFT = [
    "  internal/server/handlers/collections/list.go  missing",
    "  internal/store/collection.go:40-80  missing",
    "  internal/store/collection_test.go  missing",
]


def _branches(path: Path) -> list[str]:
    return run_git(path, "branch", "--format=%(refname:short)").split()


def _writes(gh_calls) -> list[str]:
    """Every gh call that writes, in order, with a comment's body path dropped."""
    verbs = ("item-edit", "item-add", "issue edit", "issue comment")
    return [call.split(" --body-file")[0] for call in gh_calls() if any(verb in call for verb in verbs)]


def _commit(repo: Path, subject: str, date: str | None = None) -> str:
    """An empty commit on the checked-out branch, dated `date` when given; returns its short sha."""
    env = {**os.environ, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else None
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", subject], cwd=repo, env=env, check=True)
    return run_git(repo, "rev-parse", "--short", "HEAD").strip()


def _undated_review(tmp_path: Path) -> dict[str, str]:
    """The reviewed story with its Review: entry carrying no date."""
    data = json.loads((FIXTURES / "issue-reviewed.json").read_text(encoding="utf-8"))
    for comment in data["comments"]:
        if comment["body"].startswith("Review:"):
            comment["createdAt"] = ""
    path = tmp_path / "undated.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


def _started(tmp_path: Path) -> dict[str, str]:
    """The approved story with a Started: entry already on its log."""
    data = json.loads((FIXTURES / "issue-approved.json").read_text(encoding="utf-8"))
    entry = {"author": {"login": "dev"}, "createdAt": "2026-09-03T09:00:00Z", "body": "Started: the plan holds."}
    data["comments"] = [*data.get("comments", []), entry]
    path = tmp_path / "started.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


@pytest.fixture
def origin(repo: Path, tmp_path: Path) -> Path:
    """A bare origin holding main, so there is an origin/main to branch from."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    run_git(repo, "remote", "add", "origin", str(bare))
    run_git(repo, "push", "-q", "-u", "origin", "main")
    return bare


def _start(verb: str, repo: Path, env: dict[str, str] | None = None, *args: str):
    return run_deckhand("start", verb, "248", *args, cwd=repo, env={**APPROVED, **(env or {})})


# --- apply ------------------------------------------------------------------


def test_apply_refuses_a_stub(fake_gh, gh_calls, repo, origin):
    result = run_deckhand("start", "apply", "57", "--note", "x", cwd=repo, env=STUB)

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: #57 is a stub; run /deckhand:new 57 first\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []
    assert _branches(repo) == ["main"]


def test_apply_refuses_with_an_open_blocker(fake_gh, gh_calls, repo, origin):
    result = _start("apply", repo, {"GH_BLOCKED_BY": ONE_BLOCKER}, "--note", "x")

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: blocked by #240 Grant store\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []
    assert _branches(repo) == ["main"]


def test_apply_refuses_when_not_on_the_board(fake_gh, gh_calls, repo, origin, tmp_path):
    result = _start("apply", repo, fieldvalues(tmp_path, None), "--note", "x")

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: #248 is off the board; board it first with /deckhand:next 248\n"
    assert _writes(gh_calls) == []
    assert _branches(repo) == ["main"]


def test_apply_refuses_a_story_not_in_backlog(fake_gh, gh_calls, repo, origin, tmp_path):
    result = _start("apply", repo, fieldvalues(tmp_path, "Draft"), "--note", "x")

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: #248 is Draft; a branch is cut only from Backlog\n"
    assert _writes(gh_calls) == []
    assert _branches(repo) == ["main"]


def test_apply_refuses_without_a_kind(fake_gh, gh_calls, repo, origin, tmp_path):
    result = _start("apply", repo, fieldvalues(tmp_path, "Backlog", kind=None), "--note", "x")

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: #248 has no Kind; run /deckhand:next 248\n"
    assert _writes(gh_calls) == []
    assert _branches(repo) == ["main"]


def test_apply_needs_a_note(fake_gh, repo, origin):
    result = _start("apply", repo)

    assert result.returncode == 2
    assert "--note" in result.stderr


def test_apply_refuses_a_blank_note(fake_gh, gh_calls, repo, origin):
    result = _start("apply", repo, None, "--note", "  ")

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: --note needs the pre-build check's conclusion\n"
    assert _writes(gh_calls) == []
    assert _branches(repo) == ["main"]


def test_apply_creates_a_local_branch_and_pushes_nothing(fake_gh, gh_calls, repo, origin, tmp_path):
    copy = tmp_path / "comment.md"

    result = _start("apply", repo, {"GH_BODY_FILE_COPY": str(copy)}, "--note", STARTED[9:])

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:4] == [
        f"Branch {BRANCH} created from origin/main",
        "Status=In Progress",
        "Assigned @me",
        "Logged Started",
    ]
    assert run_git(repo, "symbolic-ref", "HEAD").strip() == f"refs/heads/{BRANCH}"
    assert _branches(origin) == ["main"]
    assert _writes(gh_calls) == [IN_PROGRESS, ASSIGN, COMMENT]
    assert copy.read_text(encoding="utf-8").endswith(f"--- issue comment\n{STARTED}")


def test_apply_resumes_a_local_branch_found_by_number(fake_gh, gh_calls, repo, origin, tmp_path):
    run_git(repo, "branch", "feat/248-old-name")
    env = {**_started(tmp_path), **fieldvalues(tmp_path, "In Progress")}

    result = _start("apply", repo, env, "--note", "resumed")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Existing branch feat/248-old-name; status unchanged", "Assigned @me"]
    assert run_git(repo, "symbolic-ref", "HEAD").strip() == "refs/heads/feat/248-old-name"
    assert _writes(gh_calls) == [ASSIGN]


def test_apply_logs_the_start_a_failed_run_never_logged(fake_gh, gh_calls, repo, origin, tmp_path):
    """In Progress with no Started: entry is a start that failed after the board; the log is still owed."""
    run_git(repo, "branch", BRANCH)

    result = _start("apply", repo, fieldvalues(tmp_path, "In Progress"), "--note", STARTED[9:])

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:3] == [
        f"Existing branch {BRANCH}; status unchanged",
        "Assigned @me",
        "Logged Started",
    ]
    assert _writes(gh_calls) == [ASSIGN, COMMENT]


def test_apply_finishes_an_interrupted_start(fake_gh, gh_calls, repo, origin, tmp_path):
    """A branch cut but never boarded is the start that failed after the checkout: finish it, once."""
    copy = tmp_path / "comment.md"
    run_git(repo, "branch", BRANCH)  # the cut happened; Status is still Backlog

    result = _start("apply", repo, {"GH_BODY_FILE_COPY": str(copy)}, "--note", STARTED[9:])

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:4] == [
        f"Existing branch {BRANCH}; finishing the interrupted start",
        "Status=In Progress",
        "Assigned @me",
        "Logged Started",
    ]
    assert run_git(repo, "symbolic-ref", "HEAD").strip() == f"refs/heads/{BRANCH}"
    assert _writes(gh_calls) == [IN_PROGRESS, ASSIGN, COMMENT]
    assert copy.read_text(encoding="utf-8").count("Started:") == 1


def test_apply_refuses_to_resume_a_story_in_another_column(fake_gh, gh_calls, repo, origin, tmp_path):
    run_git(repo, "branch", BRANCH)

    result = _start("apply", repo, fieldvalues(tmp_path, "Draft"), "--note", "x")

    assert result.returncode == 1
    assert result.stderr == f"deckhand start apply: #248 has branch {BRANCH} but is Draft\n"
    assert _writes(gh_calls) == []
    assert run_git(repo, "symbolic-ref", "HEAD").strip() == "refs/heads/main"


def test_apply_refuses_two_branches_for_one_story(fake_gh, gh_calls, repo, origin):
    run_git(repo, "branch", "feat/248-one")
    run_git(repo, "branch", "fix/248-two")

    result = _start("apply", repo, None, "--note", "x")

    assert result.returncode == 1
    assert result.stderr == "deckhand start apply: this clone has 2 branches for #248: feat/248-one, fix/248-two\n"
    assert _writes(gh_calls) == []
    assert run_git(repo, "symbolic-ref", "HEAD").strip() == "refs/heads/main"


def test_apply_prints_plan_commits_and_drift(fake_gh, repo, origin, tmp_path):
    run_git(repo, "checkout", "-q", "-b", BRANCH)
    sha = _commit(repo, "feat: store method")
    run_git(repo, "checkout", "-q", "main")

    result = _start("apply", repo, fieldvalues(tmp_path, "In Progress"), "--note", "x")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("## Plan") + 1] == "# Guest Collections Implementation Plan"
    assert lines[lines.index("## Commits") : lines.index("## Plan drift")] == [
        "## Commits",
        f"  {sha} feat: store method",
    ]
    assert lines[lines.index("## Plan drift") :] == ["## Plan drift", *DRIFT, *NO_REVIEW]


# --- context ----------------------------------------------------------------


def test_context_prints_branch_blockers_plan_commits_and_drift(fake_gh, gh_calls, repo, origin):
    result = _start("context", repo, {"GH_BLOCKED_BY": ONE_BLOCKER})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:3] == [f"Branch: {BRANCH} (none)", "Blockers:", "  240  Grant store"]
    # The skill judges whether the Story and the Scope still hold, so both come before the plan.
    assert [line for line in lines if line.startswith("## ")] == [
        "## Story",
        "## Scope",
        "## Plan",
        "## Commits",
        "## Plan drift",
        LANDED,
    ]
    assert lines[lines.index("## Story") + 1].startswith("  As a guest user, I want")
    assert lines[lines.index("## Scope") + 1] == "  #### In"
    assert lines[lines.index("## Commits") : lines.index("## Plan drift")] == ["## Commits", "  none"]
    assert lines[lines.index("## Plan drift") :] == ["## Plan drift", *DRIFT, *NO_REVIEW]
    assert _branches(repo) == ["main"]
    assert _writes(gh_calls) == []


def test_context_reads_the_commits_in_a_clone_that_has_no_local_main(fake_gh, repo, origin, tmp_path):
    run_git(repo, "checkout", "-q", "-b", BRANCH)
    _commit(repo, "feat: store method")
    run_git(repo, "push", "-q", "origin", BRANCH)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--branch", BRANCH, str(origin), str(clone)], check=True)
    assert _branches(clone) == [BRANCH]
    sha = run_git(clone, "rev-parse", "--short", "HEAD").strip()

    result = _start("context", clone)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("## Commits") : lines.index("## Plan drift")] == [
        "## Commits",
        f"  {sha} feat: store method",
    ]


def test_context_reports_the_branch_the_clone_already_has(fake_gh, repo, origin):
    run_git(repo, "branch", BRANCH)

    result = _start("context", repo)

    assert result.stdout.splitlines()[0] == f"Branch: {BRANCH} (local)"


def _titled(tmp_path: Path, title: str) -> dict[str, str]:
    """The approved story with `title`, for a story retitled since its branch was cut."""
    data = json.loads((FIXTURES / "issue-approved.json").read_text(encoding="utf-8"))
    data["title"] = title
    path = tmp_path / "retitled.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


def test_context_reports_the_branch_of_a_retitled_story(fake_gh, repo, origin, tmp_path):
    run_git(repo, "branch", "feat/248-old-name")

    result = _start("context", repo, _titled(tmp_path, "Guests see granted collections only"))

    assert result.stdout.splitlines()[0] == "Branch: feat/248-old-name (local)"


def test_context_names_the_branch_without_an_origin(fake_gh, repo):
    result = _start("context", repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"Branch: {BRANCH} (none)"


def test_context_prints_what_landed_on_main_since_the_review(fake_gh, repo, origin):
    _commit(repo, "feat: landed before", "2026-08-01T00:00:00Z")
    sha = _commit(repo, "feat: landed later")
    run_git(repo, "push", "-q", "origin", "main")

    result = _start("context", repo, REVIEWED)

    lines = result.stdout.splitlines()
    assert lines[lines.index(LANDED) :] == [LANDED, f"  {sha} feat: landed later"]


def test_context_points_past_thirty_landed_commits(fake_gh, repo, origin):
    for index in range(31):  # with the init commit, thirty-two since the review
        _commit(repo, f"feat: step {index}")
    run_git(repo, "push", "-q", "origin", "main")

    result = _start("context", repo, REVIEWED)

    lines = result.stdout.splitlines()
    landed = lines[lines.index(LANDED) + 1 :]
    assert len(landed) == 31
    assert landed[0].endswith(" feat: step 30")
    assert landed[-1] == f"  ... more: git log --since={REVIEW_DATE} origin/main"


def test_context_says_when_the_review_entry_has_no_date(fake_gh, repo, origin, tmp_path):
    result = _start("context", repo, _undated_review(tmp_path))

    assert f"{LANDED}\n  the review entry has no date\n" in result.stdout


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
        "## Story",
        "## Scope",
        "## Plan",
        "## Commits",
        "## Plan drift",
        LANDED,
    ]
