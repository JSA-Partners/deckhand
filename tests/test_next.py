"""The decision table, and the command that gathers the facts it runs on and prints the briefing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand.next import Facts, decide
from tests.conftest import FIXTURES, fieldvalues, run_deckhand, run_git

BRANCH = "feat/248-x"
PR_URL = "https://github.com/acme/widgets/pull/1000"
REPO = "acme/widgets"
ISSUE_URL = f"https://github.com/{REPO}/issues/248"
TITLE = "Title: Guest users see only their granted collections"
REVIEWED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-reviewed.json")}
STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
# Two projects linked to the repository: the one failure that costs the board and nothing else.
NO_BOARD = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-two.json")}
REVIEW = "Review: sound\n\n- chaos.1, P2, accepted: A claim."
AFTER_MERGE = "### After the merge\n\n- Prove the script on main\n- Revoke the token\n"


def facts(**changes) -> Facts:
    """The facts of a story nobody has done anything with, with `changes` applied."""
    base = Facts(
        closed=False,
        status=None,
        drafted=True,
        reviewed=False,
        amended_since_review=False,
        branch=None,
        commits=None,
        pull_request=None,
        pull_requested=False,
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
        ({"closed": True}, "done", "#248 is closed."),
        ({"closed": True, "unavailable": ("board",)}, "done", "#248 is closed."),
        ({"unavailable": ("board",)}, "stop", "Cannot read board; nothing decided."),
        ({"unavailable": ("board", "branch")}, "stop", "Cannot read board, branch; nothing decided."),
        ({"pull_request": PR_URL}, "merge", f"Pull request open: {PR_URL}"),
        ({"status": "In Progress", "branch": BRANCH, "commits": 2}, "resume", f"Branch {BRANCH} has 2 commits."),
        ({"status": "In Progress", "branch": BRANCH, "commits": 0}, "build", "Started, nothing built yet."),
        ({"status": "In Progress", "branch": BRANCH}, "build", f"Branch {BRANCH} is not in this clone."),
        ({"status": "In Progress"}, "build", "No branch in this clone."),
        ({"status": "Backlog"}, "check", "On the board; check the plan against the code, then build."),
        ({"status": "Draft", "drafted": False}, "write", "#248 is a stub."),
        ({"status": None, "drafted": False}, "write", "#248 is a stub."),
        ({"status": "Draft"}, "review", "Not reviewed."),
        ({"status": None}, "review", "Not reviewed."),
        (
            {"status": "Draft", "reviewed": True, "amended_since_review": True},
            "reconsider",
            "Amended since the review.",
        ),
        ({"status": "Draft", "reviewed": True}, "board", "Reviewed, nothing waiting."),
        ({"status": None, "reviewed": True}, "board", "Reviewed, nothing waiting."),
        ({"status": "Pending Review"}, "stop", "#248 is Pending Review with no open pull request; nothing decided."),
        ({"status": "Done"}, "stop", "#248 is Done with no open pull request; nothing decided."),
        ({"status": "Parked", "reviewed": True}, "stop", "#248 is Parked with no open pull request; nothing decided."),
    ],
)
def test_the_table(changes, step, why):
    assert decide(248, facts(**changes)) == (step, why)


def test_the_rows_are_tried_in_order():
    """A closed story is done whatever else is true, and a pull request outranks the board."""
    everything = facts(
        closed=True,
        drafted=False,
        status="In Progress",
        branch=BRANCH,
        commits=2,
        pull_request=PR_URL,
        pull_requested=True,
        reviewed=True,
        amended_since_review=True,
        unavailable=("board",),
    )
    assert decide(248, everything)[0] == "done"
    assert decide(248, everything._replace(closed=False))[0] == "stop"
    assert decide(248, everything._replace(closed=False, unavailable=()))[0] == "merge"
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


def test_a_draft_story_is_reviewed_with_the_review_context(fake_gh, repo, tmp_path):
    story = _logged(tmp_path, "drafted.json", _entry("Drafted: from a brainstorm", "2026-09-01T10:00:00Z"))

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    lines = _briefing(result, "review", "Not reviewed.")
    assert lines[5] == "  2026-09-01 Drafted: from a brainstorm"
    assert _context(lines)[0] == "### Story"


def test_an_undated_entry_says_so(fake_gh, repo, tmp_path):
    story = _logged(tmp_path, "undated.json", _entry("Drafted: from a brainstorm", ""))

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    lines = _briefing(result, "review", "Not reviewed.")
    assert lines[5] == "  undated Drafted: from a brainstorm"


def test_the_log_block_says_none_without_an_entry(fake_gh, repo, tmp_path):
    """A person's comment is not an entry, so the fixture's two comments leave the log empty."""
    result = _next(repo, env=fieldvalues(tmp_path, None))

    lines = _briefing(result, "review", "Not reviewed.")
    assert lines[5:7] == ["  none", ""]


def test_the_log_block_is_the_last_three_entries(fake_gh, repo, tmp_path):
    story = _logged(
        tmp_path,
        "long.json",
        _entry("Drafted: from a brainstorm", "2026-09-01T10:00:00Z"),
        _entry(REVIEW, "2026-09-02T09:00:00Z"),
        _entry("Amended: dropped the second criterion", "2026-09-03T09:00:00Z"),
        _entry("Review: sound", "2026-09-04T09:00:00Z"),
    )
    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    lines = _briefing(result, "board", "Reviewed, nothing waiting.")
    assert lines[5:9] == [
        "  2026-09-02 Review: sound",
        "  2026-09-03 Amended: dropped the second criterion",
        "  2026-09-04 Review: sound",
        "",
    ]


def test_a_stub_is_written_with_the_new_context(fake_gh, repo, tmp_path):
    result = _next(repo, "57", env={**STUB, **fieldvalues(tmp_path, "Draft")})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: write", "#57 is a stub."]
    assert "## Stub #57" in _context(lines)


def test_a_drafted_entry_on_a_stub_body_is_still_a_stub(fake_gh, repo, tmp_path):
    """The body is what says a story was written; an entry with no story behind it moves nothing."""
    stub_body = json.loads((FIXTURES / "stub.json").read_text(encoding="utf-8"))["body"]
    story = _logged(
        tmp_path, "logged-stub.json", _entry("Drafted: from a brainstorm", "2026-09-01T10:00:00Z"), body=stub_body
    )

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Step: write", "#248 is a stub."]


def test_an_amend_after_the_review_is_reconsidered_with_the_amend_context(fake_gh, repo, tmp_path):
    story = _logged(
        tmp_path,
        "amended.json",
        _entry(REVIEW, "2026-09-02T09:00:00Z"),
        _entry("Amended: dropped the second criterion", "2026-09-03T09:00:00Z"),
    )
    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    lines = _briefing(result, "reconsider", "Amended since the review.")
    context = _context(lines)
    assert "## Body" in context
    assert "## Latest review" in context


def test_an_amend_answered_by_a_later_review_is_boarded(fake_gh, repo, tmp_path):
    """A second review clears the amend: only an `Amended:` after the latest `Review:` counts."""
    story = _logged(
        tmp_path,
        "re-reviewed.json",
        _entry(REVIEW, "2026-09-02T09:00:00Z"),
        _entry("Amended: dropped the second criterion", "2026-09-03T09:00:00Z"),
        _entry("Review: sound", "2026-09-04T09:00:00Z"),
    )
    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    lines = _briefing(result, "board", "Reviewed, nothing waiting.")
    assert _context(lines)[0].startswith("Kinds:")


def test_a_reviewed_story_off_the_board_is_boarded(fake_gh, repo, tmp_path):
    """A story from before Draft existed has no Status; reviewed and quiet, it boards like a Draft."""
    result = _next(repo, env={**REVIEWED, **fieldvalues(tmp_path, None)})

    _briefing(result, "board", "Reviewed, nothing waiting.")


def test_a_persons_comment_is_never_an_amend(fake_gh, repo, tmp_path):
    """Nothing written on GitHub by a person is read; only the log's own entries move a story."""
    story = _logged(
        tmp_path,
        "replied.json",
        _entry(REVIEW, "2026-09-02T09:00:00Z"),
        _entry("Also cover the empty case.", "2026-09-04T09:00:00Z"),
    )
    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Draft")})

    _briefing(result, "board", "Reviewed, nothing waiting.")


def test_a_backlog_story_is_checked_with_the_start_context(fake_gh, repo, tmp_path):
    result = _next(repo, env={**REVIEWED, **fieldvalues(tmp_path, "Backlog")})

    lines = _briefing(result, "check", "On the board; check the plan against the code, then build.")
    context = _context(lines)
    assert "## Plan" in context
    assert "## Landed on main since the review" in context


def test_in_progress_with_an_empty_branch_builds(fake_gh, repo, empty_branch, tmp_path):
    result = _next(repo, env=fieldvalues(tmp_path, "In Progress"))

    lines = _briefing(result, "build", "Started, nothing built yet.")
    assert "## Plan" in _context(lines)


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


def test_merge_prints_the_link_and_nothing_more(fake_gh, repo, origin, branch, tmp_path):
    result = _next(repo, env={**fieldvalues(tmp_path, "In Progress"), "GH_PR_EXISTS": "1"})

    lines = _briefing(result, "merge", f"Pull request open: {PR_URL}")
    assert lines[5:] == ["  none"]


def test_the_pull_request_is_read_from_the_log_when_the_branch_is_elsewhere(fake_gh, gh_calls, repo, tmp_path):
    """The log names the pull request; a head lookup by a name derived from today's title would miss it."""
    story = _logged(
        tmp_path,
        "opened.json",
        _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"),
        title="Guests see granted collections only",
    )
    env = {**story, **fieldvalues(tmp_path, "Pending Review"), "GH_PR_STATE": "OPEN"}

    result = _next(repo, env=env)

    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: merge", f"Pull request open: {PR_URL}"], result.stdout
    assert f"pr view {PR_URL} --repo acme/widgets --json state,url" in gh_calls()
    assert not any(call.startswith("pr list") for call in gh_calls())


def test_a_pull_request_the_log_names_that_is_no_longer_open_is_not_one(fake_gh, repo, tmp_path):
    story = _logged(tmp_path, "merged-pr.json", _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"))
    env = {**story, **fieldvalues(tmp_path, "Pending Review"), "GH_PR_STATE": "MERGED"}

    result = _next(repo, env=env)

    _briefing(result, "stop", "#248 is Pending Review with no open pull request; nothing decided.")


def test_a_merged_story_with_items_left_walks_the_after_merge_block(fake_gh, repo, tmp_path):
    body = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))["body"]
    body = body.replace("### Notes", f"{AFTER_MERGE}\n### Notes")
    story = _logged(
        tmp_path,
        "merged.json",
        _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"),
        _entry("After the merge: proved the script on main", "2026-09-06T09:00:00Z"),
        body=body,
        state="CLOSED",
    )

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Done")})

    lines = _briefing(result, "after", "#248 is merged; 1 after-the-merge item is left.")
    assert lines[5:] == [
        f"  2026-09-05 Pull request: {PR_URL}",
        "  2026-09-06 After the merge: proved the script on main",
        "Left:",
        "  - Revoke the token",
    ]


def test_a_merged_story_with_every_item_logged_is_done(fake_gh, repo, tmp_path):
    body = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))["body"]
    body = body.replace("### Notes", f"{AFTER_MERGE}\n### Notes")
    story = _logged(
        tmp_path,
        "walked.json",
        _entry(f"Pull request: {PR_URL}", "2026-09-05T09:00:00Z"),
        _entry("After the merge: proved the script on main", "2026-09-06T09:00:00Z"),
        _entry("After the merge: revoked the token", "2026-09-06T10:00:00Z"),
        _entry("After the merge: told the team", "2026-09-06T11:00:00Z"),
        body=body,
        state="CLOSED",
    )

    result = _next(repo, env={**story, **fieldvalues(tmp_path, "Done"), **_board(tmp_path)})

    lines = _briefing(result, "done", "#248 is closed.")
    assert lines[-1] == "Next story: none"
    assert "Left:" not in lines


def test_a_closed_story_with_items_left_and_no_pull_request_is_done(fake_gh, repo, tmp_path):
    """Closed without a pull request is closed by hand; nothing merged, so nothing is owed after it."""
    body = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))["body"]
    body = body.replace("### Notes", f"{AFTER_MERGE}\n### Notes")
    story = _story(tmp_path, "abandoned.json", body=body, state="CLOSED")

    result = _next(repo, env={**story, **_board(tmp_path)})

    _briefing(result, "done", "#248 is closed.")


def test_a_closed_stub_is_done(fake_gh, repo, tmp_path):
    story = _story(tmp_path, "closed-stub.json", fixture="stub.json", state="CLOSED")

    result = _next(repo, "57", env={**story, **_board(tmp_path)})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:2] == ["Step: done", "#57 is closed."]
    assert lines[-1] == "Next story: none"


def test_done_names_the_oldest_open_story_on_the_board(fake_gh, repo, tmp_path):
    env = {**_story(tmp_path, "closed.json", state="CLOSED"), **_board(tmp_path, (9, "Nine"), (4, "Four"))}

    result = _next(repo, env=env)

    lines = _briefing(result, "done", "#248 is closed.")
    assert lines[5:] == ["  none", "Next story: #4 Four"]


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
    """Pending Review with no pull request is the board's business, so there is no step to offer."""
    result = _next(repo, env=fieldvalues(tmp_path, "Pending Review"))

    lines = _briefing(result, "stop", "#248 is Pending Review with no open pull request; nothing decided.")
    assert lines[5:] == ["  none"]
