"""The decision table, and the command that gathers the facts it runs on and prints the briefing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand.next import Facts, decide
from tests.conftest import FIXTURES, advance_origin, fieldvalues, run_deckhand, run_git

BRANCH = "feat/248-x"
PR_URL = "https://github.com/acme/widgets/pull/1000"
REPO = "acme/widgets"
ISSUE_URL = f"https://github.com/{REPO}/issues/248"
TITLE = "Title: Guest users see only their granted collections"
REVIEWED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-reviewed.json")}
DRAFT = {"GH_ISSUE_FILE": str(FIXTURES / "draft.json")}
PARKED = {"GH_ISSUE_FILE": str(FIXTURES / "draft-parked.json")}
# Two projects linked to the repository: the one failure that costs the board and nothing else.
NO_BOARD = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-two.json")}
REVIEW = "Review: sound\n\n- chaos.1, P2, accepted: A claim."
AFTER_MERGE = "### After the merge\n\n- Prove the script on main\n- Revoke the token\n"
AFTER_MERGE_ONE_TICKED = "### After the merge\n\n- [x] Prove the script on main\n- [ ] Revoke the token\n"
AFTER_MERGE_ALL_TICKED = "### After the merge\n\n- [x] Prove the script on main\n- [x] Revoke the token\n"


def facts(**changes) -> Facts:
    """The facts of a story nobody has done anything with, with `changes` applied."""
    base = Facts(
        closed=False,
        status=None,
        written=True,
        branch=None,
        blockers=(),
        commits=None,
        pull_request=None,
        failed_checks=(),
        pending_checks=0,
        behind=False,
        pull_requested=False,
        merged=False,
        after_merge_left=0,
        unavailable=(),
    )
    return base._replace(**changes)


# --- the table ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "step", "why"),
    [
        (
            {"closed": True, "pull_requested": True, "after_merge_left": 2},
            "after",
            "#248 is merged; 2 after-the-merge items are left.",
        ),
        (
            {"closed": True, "pull_requested": True, "after_merge_left": 1},
            "after",
            "#248 is merged; 1 after-the-merge item is left.",
        ),
        ({"closed": True, "after_merge_left": 2}, "done", "#248 is closed."),
        ({"closed": True, "pull_requested": True}, "done", "#248 is closed."),
        (
            {"closed": True, "pull_requested": True, "status": "In Review"},
            "after",
            "#248 is merged; its column still says In Review.",
        ),
        ({"closed": True, "pull_requested": True, "status": "Done"}, "done", "#248 is closed."),
        ({"closed": True, "status": "In Review"}, "done", "#248 is closed."),
        ({"closed": True}, "done", "#248 is closed."),
        ({"closed": True, "unavailable": ("board",)}, "done", "#248 is closed."),
        ({"unavailable": ("board",)}, "stop", "Cannot read board; nothing decided."),
        ({"unavailable": ("board", "branch")}, "stop", "Cannot read board, branch; nothing decided."),
        ({"pull_request": PR_URL}, "merge", f"Pull request open: {PR_URL}"),
        (
            {"merged": True, "status": "In Review"},
            "stop",
            "#248 has a merged pull request and an open issue; close the issue on GitHub.",
        ),
        ({"status": "In Progress", "branch": BRANCH, "commits": 2}, "resume", f"Branch {BRANCH} has 2 commits."),
        ({"status": "In Progress", "branch": BRANCH, "commits": 0}, "build", "Started, nothing built yet."),
        ({"status": "In Progress", "branch": BRANCH}, "build", f"Branch {BRANCH} is not in this clone."),
        ({"status": "In Progress"}, "build", "No branch in this clone."),
        ({"status": "Backlog"}, "check", "On the board; check the plan against the code, then build."),
        ({"status": "Backlog", "blockers": ("#240  Grant store",)}, "wait", "Waits on #240."),
        ({"status": "Draft", "written": False}, "write", "#248 is a draft."),
        ({"status": None, "written": False}, "write", "#248 is a draft."),
        (
            {"status": "Draft", "written": False, "parked": True},
            "settle",
            "#248 is a parked feature; settle its requirements.",
        ),
        ({"status": "Draft"}, "review", "Not reviewed."),
        ({"status": None}, "review", "Not reviewed."),
        ({"status": "Refinement"}, "review", "Not reviewed."),
        ({"status": "Ready"}, "board", "Reviewed, nothing waiting."),
        ({"status": "In Review"}, "stop", "#248 is In Review with no open pull request; nothing decided."),
        ({"status": "Done"}, "stop", "#248 is Done with no open pull request; nothing decided."),
        ({"status": "Parked"}, "stop", "#248 is Parked with no open pull request; nothing decided."),
    ],
)
def test_the_table(changes, step, why):
    assert decide(248, facts(**changes)) == (step, why)


def test_the_rows_are_tried_in_order():
    """A closed story finishes whatever else is true, and a pull request outranks the board."""
    everything = facts(
        closed=True,
        written=False,
        status="In Progress",
        branch=BRANCH,
        commits=2,
        pull_request=PR_URL,
        pull_requested=True,
        unavailable=("board",),
    )
    assert decide(248, everything)[0] == "after"
    assert decide(248, everything._replace(status=None))[0] == "done"
    assert decide(248, everything._replace(closed=False))[0] == "stop"
    assert decide(248, everything._replace(closed=False, unavailable=()))[0] == "merge"
    assert decide(248, everything._replace(closed=False, unavailable=(), behind=True))[0] == "update"
    assert (
        decide(248, everything._replace(closed=False, unavailable=(), behind=True, failed_checks=("lint",)))[0] == "fix"
    )
    assert decide(248, everything._replace(closed=False, unavailable=(), behind=True, pending_checks=2))[0] == "update"
    assert decide(248, everything._replace(closed=False, unavailable=(), pull_request=None))[0] == "resume"


# --- the command -------------------------------------------------------------


def _story(tmp_path: Path, name: str, fixture: str = "issue.json", **changes) -> dict[str, str]:
    """The story fixture with `changes` applied, for a state no fixture file carries."""
    data = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    data.update(changes)
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


def _entry(body: str, created: str) -> dict:
    """One log entry as `gh issue view` lists a comment."""
    return {"author": {"login": "mattjmoran"}, "createdAt": created, "body": body}


def _logged(tmp_path: Path, name: str, *entries: dict, **changes) -> dict[str, str]:
    """The story fixture with `entries` as its whole log, oldest first."""
    return _story(tmp_path, name, comments=list(entries), **changes)


def _board(tmp_path: Path, *stories: tuple[int, str]) -> dict[str, str]:
    """A one-page board holding `stories` as open items of the repository."""
    nodes = [
        {
            "content": {"number": number, "closedAt": None, "title": title, "repository": {"nameWithOwner": REPO}},
            "fieldValues": {"nodes": [{"name": "Backlog", "field": {"name": "Status"}}]},
        }
        for number, title in stories
    ]
    data = {"data": {"organization": {"projectV2": {"items": {"pageInfo": {"hasNextPage": False}, "nodes": nodes}}}}}
    path = tmp_path / "items.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_PROJECT_ITEMS_FILE": str(path)}


@pytest.fixture
def origin(repo: Path, tmp_path: Path) -> Path:
    """A bare origin holding main, so there is an origin/main to count the branch against."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    run_git(repo, "remote", "add", "origin", str(bare))
    run_git(repo, "push", "-q", "-u", "origin", "main")
    return bare


@pytest.fixture
def empty_branch(repo: Path, origin: Path) -> str:
    """The story branch as start cuts it: in this clone, with nothing on it yet."""
    run_git(repo, "checkout", "-q", "-b", BRANCH)
    return BRANCH


@pytest.fixture
def branch(repo: Path, origin: Path) -> str:
    """The story branch as this clone has it: two commits past main, and nothing on origin."""
    run_git(repo, "checkout", "-q", "-b", BRANCH)
    for index in (1, 2):
        (repo / f"f{index}").write_text("x\n")
        run_git(repo, "add", f"f{index}")
        run_git(repo, "commit", "-qm", f"feat: step {index}")
    return BRANCH


def _next(repo: Path, number: str = "248", env: dict[str, str] | None = None):
    return run_deckhand("next", "context", number, cwd=repo, env=env or {})


def _briefing(result, step: str, why: str) -> list[str]:
    """The printed lines, asserting the briefing opens with `step`, `why`, the title, and the link."""
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:4] == [f"Step: {step}", why, TITLE, f"Issue: {ISSUE_URL}"]
    assert lines[4] == "Log:"
    return lines


def _context(lines: list[str]) -> list[str]:
    """The lines under `## Context`, which follow the log after one blank line."""
    index = lines.index("## Context")
    assert lines[index - 1] == ""
    return lines[index + 1 :]


def test_context_without_an_issue_names_the_captain(fake_gh):
    """A session that has just finished a story has no number in hand and no route to the board."""
    result = run_deckhand("next", "context")

    assert result.returncode == 0, result.stderr
    assert "captain" in result.stdout


def test_a_draft_story_is_reviewed_with_the_review_context(fake_gh, repo, tmp_path):
    story = _logged(tmp_path, "drafted.json", _entry("Drafted: from a brainstorm", "2026-09-01T10:00:00Z"))

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    lines = _briefing(result, "review", "Not reviewed.")
    assert lines[5] == "  2026-09-01 Drafted: from a brainstorm"
    assert _context(lines)[5].startswith("Body: ")


def test_an_undated_entry_says_so(fake_gh, repo, tmp_path):
    story = _logged(tmp_path, "undated.json", _entry("Drafted: from a brainstorm", ""))

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    lines = _briefing(result, "review", "Not reviewed.")
    assert lines[5] == "  undated Drafted: from a brainstorm"


def test_the_log_block_says_none_without_an_entry(fake_gh, repo, tmp_path):
    """A person's comment is not an entry, so the fixture's two comments leave the log empty."""
    result = _next(repo, env=fieldvalues(tmp_path, None))

    lines = _briefing(result, "review", "Not reviewed.")
    assert lines[5:8] == ["  none", "Related: none", ""]


def test_the_log_block_is_the_last_three_entries(fake_gh, repo, tmp_path):
    story = _logged(
        tmp_path,
        "long.json",
        _entry("Drafted: from a brainstorm", "2026-09-01T10:00:00Z"),
        _entry(REVIEW, "2026-09-02T09:00:00Z"),
        _entry("Amended: dropped the second criterion", "2026-09-03T09:00:00Z"),
        _entry("Review: sound", "2026-09-04T09:00:00Z"),
    )
    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Ready")})

    lines = _briefing(result, "board", "Reviewed, nothing waiting.")
    assert lines[5:10] == [
        "  2026-09-02 Review: sound",
        "  2026-09-03 Amended: dropped the second criterion",
        "  2026-09-04 Review: sound",
        "Related: none",
        "",
    ]


def test_a_stub_is_written_with_the_new_context(fake_gh, repo, tmp_path):
    result = _next(repo, "57", env={**DRAFT, **fieldvalues(tmp_path, "Draft")})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: write", "#57 is a draft."]
    assert "## Draft #57" in _context(lines)


def test_a_parked_feature_is_settled_with_the_new_context(fake_gh, repo, tmp_path):
    result = _next(repo, "60", env={**PARKED, **fieldvalues(tmp_path, "Draft")})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: settle", "#60 is a parked feature; settle its requirements."]
    assert "## Parked feature #60" in _context(lines)


PARKED_BODY = json.loads((FIXTURES / "draft-parked.json").read_text(encoding="utf-8"))["body"]
MEASURED = "Noted: every claim re-measured\n\nFour counts demoted.\nThe security finding refuted."


def test_settle_prints_every_note_in_full(fake_gh, repo, tmp_path):
    story = _logged(
        tmp_path,
        "noted.json",
        _entry("Drafted: parked from acme/widgets", "2026-09-16T10:00:00Z"),
        _entry(MEASURED, "2026-09-20T10:00:00Z"),
        body=PARKED_BODY,
    )

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    lines = result.stdout.splitlines()
    assert lines[0] == "Step: settle"
    start = lines.index("Notes:")
    assert lines[start + 1 : start + 5] == [
        "  2026-09-20 Noted: every claim re-measured",
        "",
        "  Four counts demoted.",
        "  The security finding refuted.",
    ]
    assert lines.index("Log:") < start < lines.index("## Context")


def test_only_settle_prints_the_notes(fake_gh, repo, tmp_path):
    story = _logged(tmp_path, "noted-story.json", _entry(MEASURED, "2026-09-20T10:00:00Z"))

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Backlog")})

    assert "Notes:" not in result.stdout.splitlines()


def test_a_drafted_entry_on_a_stub_body_is_still_a_stub(fake_gh, repo, tmp_path):
    """The body is what says a story was written; an entry with no story behind it moves nothing."""
    draft_body = json.loads((FIXTURES / "draft.json").read_text(encoding="utf-8"))["body"]
    story = _logged(
        tmp_path, "logged-draft.json", _entry("Drafted: from a brainstorm", "2026-09-01T10:00:00Z"), body=draft_body
    )

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Step: write", "#248 is a draft."]


def test_a_story_off_the_board_is_reviewed_again(fake_gh, repo, tmp_path):
    """No column means nothing is known about it, whatever its comments say."""
    result = _next(repo, env={**REVIEWED, **fieldvalues(tmp_path, None)})

    _briefing(result, "review", "Not reviewed.")


def test_a_backlog_story_is_checked_with_the_start_context(fake_gh, repo, tmp_path):
    result = _next(repo, env={**REVIEWED, **fieldvalues(tmp_path, "Backlog")})

    lines = _briefing(result, "check", "On the board; check the plan against the code, then build.")
    context = _context(lines)
    assert any(line.startswith("Plan: ") for line in context)
    assert "## Landed on main since the review" in context


def test_in_progress_with_an_empty_branch_builds(fake_gh, repo, empty_branch, tmp_path):
    result = _next(repo, env=fieldvalues(tmp_path, "In Progress"))

    lines = _briefing(result, "build", "Started, nothing built yet.")
    assert any(line.startswith("Plan: ") for line in _context(lines))


def test_in_progress_with_the_branch_elsewhere_builds_and_says_where_it_is_not(fake_gh, repo, origin, tmp_path):
    """A started story whose branch is on another clone still builds here; the reason says so."""
    result = _next(repo, env=fieldvalues(tmp_path, "In Progress"))

    _briefing(result, "build", "Branch feat/248-guest-users-see-only is not in this clone.")


def test_in_progress_with_no_kind_builds_with_no_branch_to_name(fake_gh, repo, tmp_path):
    result = _next(repo, env=fieldvalues(tmp_path, "In Progress", kind=None))

    _briefing(result, "build", "No branch in this clone.")


def test_a_local_branch_with_commits_resumes_with_the_start_context(fake_gh, repo, origin, branch, tmp_path):
    result = _next(repo, env=fieldvalues(tmp_path, "In Progress"))

    lines = _briefing(result, "resume", f"Branch {BRANCH} has 2 commits.")
    context = _context(lines)
    assert "## Commits" in context
    assert any(line.endswith("feat: step 2") for line in context)


def test_the_count_is_past_what_has_landed_on_main_since_the_branch_was_cut(fake_gh, repo, origin, branch, tmp_path):
    """Main moving on under the branch is not the story's work, and a stale origin/main must not count it."""
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    run_git(other, "config", "user.email", "t@t")
    run_git(other, "config", "user.name", "t")
    (other / "landed").write_text("x\n")
    run_git(other, "add", "landed")
    run_git(other, "commit", "-qm", "feat: landed on main")
    run_git(other, "push", "-q", "origin", "main")
    landed = run_git(other, "rev-parse", "HEAD").strip()

    result = _next(repo, env=fieldvalues(tmp_path, "In Progress"))

    _briefing(result, "resume", f"Branch {BRANCH} has 2 commits.")
    assert run_git(repo, "rev-parse", "origin/main").strip() == landed


def test_the_branch_is_found_by_its_number_after_a_retitle(fake_gh, repo, origin, branch, tmp_path):
    """The slug in the branch name is the title's on the day start ran; the number is the story's for good."""
    retitled = _story(tmp_path, "retitled.json", title="Guests see granted collections only")

    result = _next(repo, env={**retitled, **fieldvalues(tmp_path, "In Progress")})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Step: resume", f"Branch {BRANCH} has 2 commits."]


def test_a_story_without_a_kind_has_no_branch_to_read(fake_gh, repo, tmp_path):
    """No Kind means no branch, which is an answer, not a failed read."""
    result = _next(repo, env=fieldvalues(tmp_path, "Draft", kind=None))

    _briefing(result, "review", "Not reviewed.")
    assert "unavailable" not in result.stdout


def test_a_failed_check_routes_to_the_fix():
    assert decide(248, facts(pull_request=PR_URL, failed_checks=("Unit tests",))) == (
        "fix",
        "Pull request open; Unit tests failed.",
    )


def test_running_checks_are_named_on_the_merge_row():
    assert decide(248, facts(pull_request=PR_URL, pending_checks=2)) == (
        "merge",
        "Pull request open; 2 checks are still running.",
    )


def test_a_failed_check_prints_the_start_context(fake_gh, repo, origin, branch, tmp_path):
    checks = json.dumps([{"name": "Unit tests", "conclusion": "FAILURE"}, {"name": "Lint", "conclusion": "SUCCESS"}])
    env = {
        **fieldvalues(tmp_path, "In Review"),
        "GH_PR_EXISTS": "1",
        "GH_PR_STATE": "OPEN",
        "GH_PR_CHECKS": checks,
    }

    result = _next(repo, env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: fix", "Pull request open; Unit tests failed."]
    assert "## Context" in lines
    assert any(line.startswith("Branch: ") for line in lines)


def test_merge_prints_the_link_and_nothing_more(fake_gh, repo, origin, branch, tmp_path):
    result = _next(repo, env={**fieldvalues(tmp_path, "In Progress"), "GH_PR_EXISTS": "1"})

    lines = _briefing(result, "merge", f"Pull request open: {PR_URL}")
    assert lines[5:] == ["  none", "Related: none"]


def test_the_pull_request_is_found_when_the_branch_is_elsewhere(fake_gh, gh_calls, repo, tmp_path):
    """The issue links to its pull request; a head lookup by a name from today's title would miss it."""
    story = _logged(
        tmp_path,
        "opened.json",
        _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"),
        title="Guests see granted collections only",
    )
    env = {**story, **fieldvalues(tmp_path, "In Review"), "GH_PR_STATE": "OPEN"}

    result = _next(repo, env=env)

    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: merge", f"Pull request open: {PR_URL}"], result.stdout
    assert any("closedByPullRequestsReferences" in call for call in gh_calls())
    assert not any(call.startswith("pr list") for call in gh_calls())


def test_an_unread_merge_state_stops_rather_than_reading_as_up_to_date(fake_gh, repo, tmp_path):
    """GitHub works mergeability out lazily, and not knowing is not the same as knowing main has not moved."""
    story = _logged(tmp_path, "unknown.json", _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"))
    env = {
        **story,
        **fieldvalues(tmp_path, "In Review"),
        "GH_PR_STATE": "OPEN",
        "GH_PR_MERGE_STATE": "UNKNOWN",
    }

    result = _next(repo, env=env)

    _briefing(result, "stop", "Cannot read merge state; nothing decided.")


def test_a_merged_pull_request_with_an_open_issue_says_to_close_it(fake_gh, repo, tmp_path):
    """The squash landed and the Closes footer did not fire, so the story is nobody's until it closes."""
    story = _logged(tmp_path, "merged-pr.json", _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"))
    env = {**story, **fieldvalues(tmp_path, "In Review"), "GH_PR_STATE": "MERGED"}

    result = _next(repo, env=env)

    _briefing(result, "stop", "#248 has a merged pull request and an open issue; close the issue on GitHub.")


def test_a_pull_request_closed_without_merging_is_not_one(fake_gh, repo, tmp_path):
    story = _logged(tmp_path, "closed-pr.json", _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"))
    env = {**story, **fieldvalues(tmp_path, "In Review"), "GH_PR_STATE": "CLOSED"}

    result = _next(repo, env=env)

    _briefing(result, "stop", "#248 is In Review with no open pull request; nothing decided.")


def test_a_closed_story_merged_through_an_unlinked_pull_request_walks_the_after_step(fake_gh, repo, tmp_path):
    """GitHub sometimes misses the Closes footer, so the head lookup finds the merge the issue does not link."""
    story = _story(tmp_path, "unlinked.json", state="CLOSED")
    merged = json.dumps([{"url": PR_URL, "state": "MERGED"}])
    env = {**story, **fieldvalues(tmp_path, "In Review"), "GH_PR_LIST_BODY": merged}

    result = _next(repo, env=env)

    _briefing(result, "after", "#248 is merged; its column still says In Review.")


def test_an_open_story_merged_through_an_unlinked_pull_request_says_to_close_it(fake_gh, repo, tmp_path):
    merged = json.dumps([{"url": PR_URL, "state": "MERGED"}])
    env = {**fieldvalues(tmp_path, "In Review"), "GH_PR_LIST_BODY": merged}

    result = _next(repo, env=env)

    _briefing(result, "stop", "#248 has a merged pull request and an open issue; close the issue on GitHub.")


def test_a_merged_story_with_items_left_walks_the_after_merge_block(fake_gh, repo, tmp_path):
    body = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))["body"]
    body = body.replace("### Notes", f"{AFTER_MERGE_ONE_TICKED}\n### Notes")
    story = _logged(
        tmp_path,
        "merged.json",
        _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"),
        _entry("After the merge: proved the script on main", "2026-09-06T09:00:00Z"),
        body=body,
        state="CLOSED",
    )

    env = {**story, **fieldvalues(tmp_path, "Done"), "GH_PR_STATE": "MERGED"}

    result = _next(repo, env=env)

    lines = _briefing(result, "after", "#248 is merged; 1 after-the-merge item is left.")
    assert lines[5:7] == [
        f"  2026-09-05 Pull request: {PR_URL}",
        "  2026-09-06 After the merge: proved the script on main",
    ]
    assert "  1. [x] Prove the script on main" in lines
    assert "  2. [ ] Revoke the token" in lines
    assert _context(lines)[0] == "Apply: deckhand after apply 248 --item 2"
    assert lines[-1] == "Clear: 1 after-the-merge item left"


def test_the_items_left_come_from_the_boxes_without_any_log_entry(fake_gh, repo, tmp_path):
    body = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))["body"]
    body = body.replace("### Notes", f"{AFTER_MERGE_ONE_TICKED}\n### Notes")
    story = _logged(
        tmp_path,
        "boxed.json",
        _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"),
        body=body,
        state="CLOSED",
    )

    env = {**story, **fieldvalues(tmp_path, "Done"), "GH_PR_STATE": "MERGED"}

    result = _next(repo, env=env)

    lines = _briefing(result, "after", "#248 is merged; 1 after-the-merge item is left.")
    # The boxes are what say an item is done, so the context names the second one with no log entry
    # about the first anywhere on the issue.
    assert "  1. [x] Prove the script on main" in lines
    assert "  2. [ ] Revoke the token" in lines
    assert _context(lines)[0] == "Apply: deckhand after apply 248 --item 2"
    assert lines[-1] == "Clear: 1 after-the-merge item left"


def test_a_merged_story_with_every_item_ticked_is_done(fake_gh, repo, tmp_path):
    body = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))["body"]
    body = body.replace("### Notes", f"{AFTER_MERGE_ALL_TICKED}\n### Notes")
    story = _logged(
        tmp_path,
        "walked.json",
        _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"),
        body=body,
        state="CLOSED",
    )

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Done"), **_board(tmp_path)})

    lines = _briefing(result, "done", "#248 is closed.")
    assert lines[-2:] == ["Clear: nothing owed", "Next story: none"]
    assert "Left:" not in lines


def test_a_done_story_names_work_the_checkout_would_lose(fake_gh, repo, tmp_path):
    """The question after a merge is whether the session can go, and unsaved work is the answer to it."""
    (repo / "scratch.txt").write_text("unsaved\n", encoding="utf-8")
    story = _story(tmp_path, "closed.json", state="CLOSED")

    result = _next(repo, env={**story, **_board(tmp_path)})

    lines = _briefing(result, "done", "#248 is closed.")
    assert f"Clear: uncommitted changes in {repo.resolve()}" in lines


def test_a_session_in_a_worktree_hears_that_its_folder_goes(fake_gh, repo, tmp_path):
    other = _worktree(repo, "feat/900-somewhere")
    story = _story(tmp_path, "closed.json", state="CLOSED")

    result = run_deckhand("next", "context", "248", cwd=other, env={**story, **_board(tmp_path)})

    lines = _briefing(result, "done", "#248 is closed.")
    assert "Clear: nothing owed" in lines
    assert f"  this session stands in the worktree {other.resolve()}; its folder goes on the next run" in " ".join(
        lines
    )


def test_a_closed_story_with_items_left_and_no_pull_request_is_done(fake_gh, repo, tmp_path):
    """Closed without a pull request is closed by hand; nothing merged, so nothing is owed after it."""
    body = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))["body"]
    body = body.replace("### Notes", f"{AFTER_MERGE}\n### Notes")
    story = _story(tmp_path, "abandoned.json", body=body, state="CLOSED")

    result = _next(repo, env={**story, **_board(tmp_path)})

    _briefing(result, "done", "#248 is closed.")


def test_a_closed_stub_is_done(fake_gh, repo, tmp_path):
    story = _story(tmp_path, "closed-draft.json", fixture="draft.json", state="CLOSED")

    result = _next(repo, "57", env={**story, **_board(tmp_path)})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: done", "#57 is closed."]
    assert lines[-1] == "Next story: none"


def test_done_names_the_oldest_open_story_on_the_board(fake_gh, repo, tmp_path):
    env = {**_story(tmp_path, "closed.json", state="CLOSED"), **_board(tmp_path, (9, "Nine"), (4, "Four"))}

    result = _next(repo, env=env)

    lines = _briefing(result, "done", "#248 is closed.")
    assert lines[5:] == ["  none", "Clear: nothing owed", "Next story: #4 Four"]


def test_done_with_an_empty_board_says_so(fake_gh, repo, tmp_path):
    env = {**_story(tmp_path, "closed.json", state="CLOSED"), **_board(tmp_path)}

    result = _next(repo, env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "Next story: none"


def test_a_closed_issue_is_done_even_when_the_board_cannot_be_read(fake_gh, repo, tmp_path):
    env = {**_story(tmp_path, "closed.json", state="CLOSED"), **NO_BOARD}

    result = _next(repo, env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "Step: done" in lines
    assert "#248 is closed." in lines
    assert lines[-1].startswith("Next story: unavailable (")


# --- a fact that could not be read -------------------------------------------


def _stopped(result, fact: str) -> list[str]:
    """The printed lines, asserting the command stopped on `fact` and offered no step's context."""
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    index = lines.index("Step: stop")
    assert lines[index + 1] == f"Cannot read {fact}; nothing decided."
    assert "## Context" not in lines
    assert "Left:" not in lines
    assert not any(line.startswith("Next story:") for line in lines)
    return lines


def test_an_unreadable_repository_stops(no_real_gh, repo):
    """With gh unreachable nothing is known, and the command still exits 0."""
    result = _next(repo)

    lines = _stopped(result, "repository")
    assert "Traceback" not in result.stderr
    assert [line for line in lines if "unavailable" in line] == [lines[0]]
    assert lines[0].startswith("Repository: unavailable (")
    assert lines[1:] == ["Step: stop", "Cannot read repository; nothing decided.", "Log:", "  none"]


def test_an_unreadable_board_stops_and_says_so_once(fake_gh, repo):
    result = _next(repo, env=NO_BOARD)

    lines = _stopped(result, "board")
    # The branch is found by the number, so an unreadable board costs one line, not two.
    assert [line for line in lines if "unavailable" in line] == [lines[0]]
    assert lines[0].startswith("Board: unavailable (")
    assert lines[3:5] == [TITLE, f"Issue: {ISSUE_URL}"]


def test_two_branches_for_one_story_stop(fake_gh, repo, origin, branch, tmp_path):
    run_git(repo, "branch", "feat/248-another-name", "main")

    result = _next(repo, env=fieldvalues(tmp_path, "In Progress"))

    lines = _stopped(result, "branch")
    assert lines[0] == "Branch: unavailable (this clone has 2 branches for #248: feat/248-another-name, feat/248-x)"


def test_an_underivable_branch_stops(fake_gh, repo, tmp_path):
    env = {**_story(tmp_path, "untitled.json", title="***"), **fieldvalues(tmp_path, "Backlog")}

    result = _next(repo, env=env)

    lines = _stopped(result, "branch")
    assert lines[0].startswith("Branch: unavailable (")


def test_an_unreadable_pull_request_stops(fake_gh, repo, origin, branch, tmp_path):
    env = {**fieldvalues(tmp_path, "In Progress"), "GH_PR_LIST_FAILS": "1"}

    result = _next(repo, env=env)

    lines = _stopped(result, "pull request")
    assert lines[0].startswith("Pull request: unavailable (")


def test_a_story_in_a_column_next_cannot_act_on_stops(fake_gh, repo, tmp_path):
    """In Review with no pull request is the board's business, so there is no step to offer."""
    result = _next(repo, env=fieldvalues(tmp_path, "In Review"))

    lines = _briefing(result, "stop", "#248 is In Review with no open pull request; nothing decided.")
    assert lines[5:] == ["  none", "Related: none"]


# --- worktrees ----------------------------------------------------------------


def _worktree(repo: Path, branch: str) -> Path:
    """The story's worktree, made where start would put it, on a new branch from main."""
    path = repo.resolve() / ".claude" / "worktrees" / branch
    run_git(repo, "worktree", "add", str(path), "-b", branch)
    return path


def _closed_story(tmp_path: Path, number: int) -> dict[str, str]:
    """A closed story under the fake gh's per-number switch, for the sweep to find."""
    data = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
    data.update(number=number, state="CLOSED")
    path = tmp_path / f"issue-{number}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {f"GH_ISSUE_FILE_{number}": str(path)}


def test_the_briefing_names_the_worktree_when_the_branch_is_checked_out_elsewhere(fake_gh, repo, origin, tmp_path):
    path = _worktree(repo, BRANCH)

    result = _next(repo, env=fieldvalues(tmp_path, "In Progress"))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:6] == [
        "Step: build",
        "Started, nothing built yet.",
        TITLE,
        f"Issue: {ISSUE_URL}",
        f"Worktree: {path}",
        "Log:",
    ]


def test_the_briefing_has_no_worktree_line_from_inside_it(fake_gh, repo, origin, tmp_path):
    path = _worktree(repo, BRANCH)

    result = run_deckhand("next", "context", "248", cwd=path, env=fieldvalues(tmp_path, "In Progress"))

    _briefing(result, "build", "Started, nothing built yet.")
    assert "Worktree:" not in result.stdout


def test_the_briefing_lists_what_the_story_names(fake_gh, repo, origin, tmp_path):
    data = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
    data["body"] = data["body"].replace("### Notes", "### Notes\n\nFollows #253 and #257.", 1)
    path = tmp_path / "named.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = _next(repo, env={"GH_ISSUE_FILE": str(path), **fieldvalues(tmp_path, "Backlog")})

    lines = result.stdout.splitlines()
    start = lines.index("Related:")
    assert lines[start + 1 : start + 3] == [
        "  #253 closed Done Seed the role matrix",
        "  #257 open Backlog Export the role matrix",
    ]
    assert lines.index("Log:") < start < lines.index("## Context")


def test_a_blocker_is_not_listed_again_under_related(fake_gh, repo, tmp_path):
    data = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
    data["body"] += "\n\nNeeds acme/gadgets#9 and #253."
    (tmp_path / "named.json").write_text(json.dumps(data), encoding="utf-8")
    env = {
        "GH_ISSUE_FILE": str(tmp_path / "named.json"),
        "GH_BLOCKED_BY": ELSEWHERE,
        **fieldvalues(tmp_path, "Backlog"),
    }

    lines = _next(repo, env=env).stdout.splitlines()

    assert "  acme/gadgets#9  Endpoint" in lines
    start = lines.index("Related:")
    assert [line for line in lines[start + 1 :] if "gadgets#9" in line] == []
    assert "  #253 closed Done Seed the role matrix" in lines[start + 1 :]


def test_the_briefing_survives_a_failed_related_query(fake_gh, repo, origin, tmp_path):
    data = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
    data["body"] += "\n\nFollows #253."
    (tmp_path / "named.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    env = {
        "GH_ISSUE_FILE": str(tmp_path / "named.json"),
        "GH_RELATED_ERROR": "boom",
        "GH_RELATED_FILE": str(tmp_path / "bad.json"),
        **fieldvalues(tmp_path, "Backlog"),
    }

    result = _next(repo, env=env)

    assert "Related: unavailable (boom)" in result.stdout.splitlines()
    assert "## Context" in result.stdout.splitlines()


def test_the_briefing_says_when_nothing_is_related(fake_gh, repo, origin, tmp_path):
    result = _next(repo, env=fieldvalues(tmp_path, "Backlog"))

    assert "Related: none" in result.stdout.splitlines()


def test_done_from_the_clone_removes_the_worktree_and_the_branch(fake_gh, repo, origin, tmp_path):
    path = _worktree(repo, BRANCH)
    env = {**_story(tmp_path, "closed.json", state="CLOSED"), **fieldvalues(tmp_path, "Done"), **_board(tmp_path)}

    result = _next(repo, env=env)

    lines = _briefing(result, "done", f"#248 is closed. Removed worktree {path}.")
    assert "Worktree:" not in result.stdout
    assert not path.exists()
    assert run_git(repo, "branch", "--format=%(refname:short)").split() == ["main"]
    assert lines[-1] == "Next story: none"


def test_done_from_inside_the_worktree_only_says_where_it_is(fake_gh, repo, origin, tmp_path):
    path = _worktree(repo, BRANCH)
    env = {**_story(tmp_path, "closed.json", state="CLOSED"), **fieldvalues(tmp_path, "Done"), **_board(tmp_path)}

    result = run_deckhand("next", "context", "248", cwd=path, env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:5] == ["Step: done", "#248 is closed.", TITLE, f"Issue: {ISSUE_URL}", f"Worktree: {path} (here)"]
    assert path.exists()


def test_done_from_the_clone_leaves_a_worktree_with_changes_and_says_so(fake_gh, repo, origin, tmp_path):
    path = _worktree(repo, BRANCH)
    (path / "wip").write_text("x\n", encoding="utf-8")
    env = {**_story(tmp_path, "closed.json", state="CLOSED"), **fieldvalues(tmp_path, "Done"), **_board(tmp_path)}

    result = _next(repo, env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:4] == ["Step: done", "#248 is closed.", TITLE, f"Issue: {ISSUE_URL}"]
    assert lines[4].startswith(f"Worktree: {path} (left: ")
    assert lines[5] == "Log:"
    assert path.exists()


def test_the_sweep_runs_from_the_clone_before_the_briefing(fake_gh, repo, origin, tmp_path):
    finished = _worktree(repo, "fix/57-finished")
    env = {**_closed_story(tmp_path, 57), **fieldvalues(tmp_path, "Backlog")}

    result = _next(repo, env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == [f"Removed worktree {finished}", "Step: check"]
    assert not finished.exists()


def test_the_sweep_never_runs_from_a_worktree(fake_gh, repo, origin, tmp_path):
    finished = _worktree(repo, "fix/57-finished")
    mine = _worktree(repo, BRANCH)
    env = {**_closed_story(tmp_path, 57), **fieldvalues(tmp_path, "In Progress")}

    result = run_deckhand("next", "context", "248", cwd=mine, env=env)

    _briefing(result, "build", "Started, nothing built yet.")
    assert "Removed" not in result.stdout
    assert finished.exists()


# --- blockers and the update row ------------------------------------------------

ELSEWHERE = '[{"number":9,"title":"Endpoint","state":"open","repository":{"full_name":"acme/gadgets"}}]'


def test_a_pull_request_behind_main_is_updated(fake_gh, repo, origin, branch, tmp_path):
    story = _logged(tmp_path, "pr.json", _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"))
    env = {**story, **fieldvalues(tmp_path, "In Review"), "GH_PR_STATE": "OPEN", "GH_PR_MERGE_STATE": "BEHIND"}

    result = _next(repo, env=env)

    lines = _briefing(result, "update", "Pull request open; main has moved on.")
    assert "## Context" not in lines


def test_the_briefing_names_open_blockers_on_the_build_row(fake_gh, repo, empty_branch, tmp_path):
    result = _next(repo, env={**fieldvalues(tmp_path, "In Progress"), "GH_BLOCKED_BY": ELSEWHERE})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: build", "Started, nothing built yet."]
    assert lines[4:7] == ["Blocked by:", "  acme/gadgets#9  Endpoint", "Log:"]


def test_the_briefing_names_open_blockers_on_the_resume_row(fake_gh, repo, origin, branch, tmp_path):
    result = _next(repo, env={**fieldvalues(tmp_path, "In Progress"), "GH_BLOCKED_BY": ELSEWHERE})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: resume", f"Branch {BRANCH} has 2 commits."]
    assert lines[4:6] == ["Blocked by:", "  acme/gadgets#9  Endpoint"]


def test_a_blocked_backlog_story_waits_and_is_given_no_plan(fake_gh, repo, tmp_path):
    """The session stops at once: reading a plan it cannot build costs a briefing for nothing."""
    result = _next(repo, env={**fieldvalues(tmp_path, "Backlog"), "GH_BLOCKED_BY": ELSEWHERE})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: wait", "Waits on acme/gadgets#9."]
    assert lines[4:7] == ["Blocked by:", "  acme/gadgets#9  Endpoint", "Log:"]
    assert "## Context" not in result.stdout


def test_a_context_reads_each_fact_once(fake_gh, gh_calls, repo, tmp_path):
    """Every step's context runs inside one cache, so the briefing and the step's own context share a read."""
    story = _logged(tmp_path, "cached.json", _entry("Drafted: from a brainstorm", "2026-09-01T10:00:00Z"))

    _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    assert len([c for c in gh_calls() if c.startswith("issue view 248")]) == 1


def test_a_briefing_reads_the_pull_request_once(fake_gh, gh_calls, repo, tmp_path):
    """The state, the checks and the merge state are one read of the issue's own reference to it."""
    story = _logged(tmp_path, "pr.json", _entry("Pull request: " + PR_URL, "2026-09-05T10:00:00Z"))

    env = {**story, **fieldvalues(tmp_path, "In Review"), "GH_PR_STATE": "OPEN"}

    _next(repo, env=env)

    assert [c for c in gh_calls() if c.startswith("pr view")] == []
    assert len([c for c in gh_calls() if "closedByPullRequestsReferences" in c]) == 1


def test_next_catches_a_stale_clone_up_before_it_briefs(fake_gh, repo, origin, tmp_path):
    advance_origin(origin, tmp_path, count=1)

    result = _next(repo)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "Caught up: main moved 1 commit to origin/main."
    assert lines[1].startswith("Step: ")
