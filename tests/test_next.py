"""The decision table, and the command that gathers the facts it runs on."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from deckhand.next import Facts, decide, instructions
from tests.conftest import FIXTURES, ROOT, run_deckhand

BRANCH = "feat/248-guest-users-see-only"
PR_URL = "https://github.com/acme/widgets/pull/1000"
ISSUE_URL = "https://github.com/acme/widgets/issues/248"
REVIEWED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-reviewed.json")}
STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
# Two projects linked to the repository: the one failure that costs the board and nothing else.
NO_BOARD = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-two.json")}
REVIEW = "## Review\n\n- [ ] spec.1"


def facts(**changes) -> Facts:
    """The four facts of a story nobody has done anything with, with `changes` applied."""
    base = Facts(
        closed=False,
        stub=False,
        status=None,
        branch=None,
        branch_commits=None,
        pull_request=None,
        reviewed=False,
        feedback=False,
        unavailable=(),
    )
    return base._replace(**changes)


# --- the table ---------------------------------------------------------------


def test_a_closed_issue_is_done():
    assert decide(248, facts(closed=True)) == ("done", "#248 is closed.")


def test_a_fact_that_could_not_be_read_stops_rather_than_guesses():
    assert decide(248, facts(unavailable=("board",))) == ("stop", "Cannot read board; nothing decided.")
    assert decide(248, facts(unavailable=("board", "comments")))[1] == "Cannot read board, comments; nothing decided."


def test_a_closed_issue_is_done_even_with_a_fact_missing():
    """Closed is the one answer nothing else can change, so it is read before the missing facts."""
    assert decide(248, facts(closed=True, unavailable=("board",))) == ("done", "#248 is closed.")


def test_an_open_pull_request_means_merge():
    assert decide(248, facts(pull_request=PR_URL)) == ("merge", f"Pull request open: {PR_URL}")


def test_in_progress_with_commits_is_a_choice():
    story = facts(status="In Progress", branch=BRANCH, branch_commits=2)

    assert decide(248, story) == ("choose", f"Branch {BRANCH} has 2 commits.")


def test_in_progress_without_commits_resumes_start():
    assert decide(248, facts(status="In Progress", branch_commits=0)) == ("start", "Started, nothing built yet.")
    assert decide(248, facts(status="In Progress")) == ("start", "Started, nothing built yet.")


def test_backlog_starts():
    assert decide(248, facts(status="Backlog")) == ("start", "On the board.")


def test_backlog_with_feedback_is_amended_before_it_is_started():
    """A story still in Backlog answers its review first; nothing branches on an unanswered tick."""
    story = facts(status="Backlog", reviewed=True, feedback=True)

    assert decide(248, story) == ("amend", "Feedback waiting on the issue.")


def test_a_status_with_no_pull_request_stops():
    """Pending Review and Done are the board's own columns; nothing reboards a story out of one."""
    assert decide(248, facts(status="Pending Review")) == (
        "stop",
        "#248 is Pending Review with no open pull request; nothing decided.",
    )
    assert decide(248, facts(status="Done")) == (
        "stop",
        "#248 is Done with no open pull request; nothing decided.",
    )
    assert decide(248, facts(status="Parked", reviewed=True))[0] == "stop"


def test_a_stub_is_written():
    assert decide(57, facts(stub=True)) == ("write", "#57 is a stub.")


def test_no_review_means_review():
    assert decide(248, facts()) == ("review", "Not reviewed.")


def test_feedback_means_amend():
    assert decide(248, facts(reviewed=True, feedback=True)) == ("amend", "Feedback waiting on the issue.")


def test_reviewed_and_quiet_means_ready():
    assert decide(248, facts(reviewed=True)) == ("ready", "Reviewed, nothing waiting.")


def test_the_rows_are_tried_in_order():
    """A closed story is done whatever else is true, and a pull request outranks the board."""
    everything = facts(
        closed=True,
        stub=True,
        status="In Progress",
        branch=BRANCH,
        branch_commits=2,
        pull_request=PR_URL,
        reviewed=True,
        feedback=True,
    )
    assert decide(248, everything)[0] == "done"
    assert decide(248, everything._replace(closed=False))[0] == "merge"
    assert decide(248, everything._replace(closed=False, pull_request=None))[0] == "choose"


# --- the instructions --------------------------------------------------------


def test_instructions_drop_the_frontmatter_and_the_injection_line():
    text = instructions("review", 248)

    assert text.startswith("# Review 248")
    assert "name: review" not in text
    assert "!`" not in text
    assert "$issue" not in text


def test_a_stub_is_written_with_the_new_skills_instructions():
    assert instructions("write", 57).startswith("# New")


def test_instructions_name_the_deckhand_that_printed_them(monkeypatch, tmp_path):
    """A skill's launcher is a placeholder Claude Code fills; a printed command has to run as it is."""
    launcher = tmp_path / "bin" / "deckhand"
    monkeypatch.setattr(sys, "argv", [str(launcher)])

    text = instructions("amend", 248)

    assert "${CLAUDE_PLUGIN_ROOT}" not in text
    assert f'"{launcher}" amend apply 248' in text


# --- the command -------------------------------------------------------------


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


def _fieldvalues(tmp_path: Path, status: str | None, kind: str | None = "feat") -> dict[str, str]:
    """The field values fixture with Status and Kind set, or removed where they are None."""
    data = json.loads((FIXTURES / "graphql-fieldvalues.json").read_text(encoding="utf-8"))
    values = data["data"]["node"]["fieldValues"]
    wanted = {"Status": status, "Kind": kind}
    nodes = []
    for node in values["nodes"]:
        name = (node.get("field") or {}).get("name")
        if name not in wanted:
            nodes.append(node)
        elif wanted[name] is not None:
            nodes.append({**node, "name": wanted[name]})
    values["nodes"] = nodes
    path = tmp_path / f"fieldvalues-{(status or 'none')}-{kind or 'none'}.json".replace(" ", "-").lower()
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_FIELDVALUES_FILE": str(path)}


def _story(tmp_path: Path, name: str, **changes) -> dict[str, str]:
    """The story fixture with `changes` applied, for a state no fixture file carries."""
    data = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
    data.update(changes)
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


def _comments(tmp_path: Path, *raw: dict) -> dict[str, str]:
    """A REST comments listing for the fake to answer with."""
    path = tmp_path / "comments.json"
    path.write_text(json.dumps(list(raw)), encoding="utf-8")
    return {"GH_COMMENTS_FILE": str(path)}


def _raw(body: str, created: str, updated: str | None = None, login: str = "arjan") -> dict:
    return {"body": body, "created_at": created, "updated_at": updated or created, "user": {"login": login}}


@pytest.fixture
def origin(repo: Path, tmp_path: Path) -> Path:
    """A bare origin holding main, so the story branch has somewhere to be."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return bare


@pytest.fixture
def branch(repo: Path, origin: Path) -> str:
    """The story branch as origin has it: two commits past main."""
    _git(repo, "checkout", "-q", "-b", BRANCH)
    for index in (1, 2):
        (repo / f"f{index}").write_text("x\n")
        _git(repo, "add", f"f{index}")
        _git(repo, "commit", "-qm", f"feat: step {index}")
    _git(repo, "push", "-q", "-u", "origin", BRANCH)
    return BRANCH


def _next(repo: Path, number: str = "248", env: dict[str, str] | None = None):
    return run_deckhand("next", "context", number, cwd=repo, env=env or {})


def test_context_prints_the_step_its_reason_the_instructions_and_the_context(fake_gh, repo, tmp_path):
    result = _next(repo, env=_fieldvalues(tmp_path, None))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:5] == ["Step: review", "Not reviewed.", "", "## Instructions", "# Review 248"]
    assert "$issue" not in result.stdout
    assert "!`" not in result.stdout
    # The step's own context follows its instructions, and review's opens with the story body.
    tail = lines[lines.index("## Context") :]
    assert tail[1] == "### Story"


def test_context_dispatches_a_stub_to_the_new_step(fake_gh, repo, tmp_path):
    result = _next(repo, "57", env={**STUB, **_fieldvalues(tmp_path, None)})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:4] == ["Step: write", "#57 is a stub.", "", "## Instructions"]
    assert "## Stub #57" in result.stdout


def test_choose_prints_the_branch_and_the_menu(fake_gh, repo, origin, branch, tmp_path):
    result = _next(repo, env=_fieldvalues(tmp_path, "In Progress"))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:3] == [
        "Step: choose",
        f"Branch {BRANCH} has 2 commits.",
        "Ask one question: Keep building, or Review and finish. "
        "Keep building runs the start step; Review and finish runs the finish step.",
    ]
    assert "## If keep building" in lines
    assert "## If finish" in lines
    assert lines.index("## If keep building") < lines.index("## If finish")
    launcher = str((ROOT / "bin" / "deckhand").resolve())
    assert lines[lines.index("## If keep building") + 2] == f'Run "{launcher}" start context 248 first, then:'
    assert lines[lines.index("## If finish") + 2] == (
        f'Check out the branch, then run "{launcher}" finish context 248, then:'
    )
    assert "${CLAUDE_PLUGIN_ROOT}" not in result.stdout


def test_in_progress_with_an_empty_branch_resumes_start(fake_gh, repo, origin, tmp_path):
    result = _next(repo, env=_fieldvalues(tmp_path, "In Progress"))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Step: start", "Started, nothing built yet."]


def test_a_story_without_a_kind_has_no_branch_to_read(fake_gh, repo, tmp_path):
    """No Kind means no branch, which is an answer, not a failed read."""
    result = _next(repo, env=_fieldvalues(tmp_path, None, kind=None))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Step: review", "Not reviewed."]
    assert "unavailable" not in result.stdout


def test_merge_prints_the_link(fake_gh, repo, origin, branch, tmp_path):
    result = _next(repo, env={**_fieldvalues(tmp_path, "In Progress"), "GH_PR_EXISTS": "1"})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Step: merge",
        f"Pull request open: {PR_URL}",
        "Merge the pull request on GitHub.",
        "Next: /deckhand:next 248 after it merges.",
    ]


def test_done_names_the_issue_and_the_stories_it_blocks(fake_gh, repo, tmp_path):
    blocking = json.dumps([{"number": 251, "state": "open"}, {"number": 250, "state": "open"}])
    env = {**_story(tmp_path, "issue-closed.json", state="CLOSED"), "GH_BLOCKING": blocking}

    result = _next(repo, env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Step: done",
        "#248 is closed.",
        f"Issue: {ISSUE_URL}",
        "Next: /deckhand:next 250",
        "Next: /deckhand:next 251",
    ]


def test_done_with_nothing_waiting_says_so(fake_gh, repo, tmp_path):
    result = _next(repo, env=_story(tmp_path, "issue-closed.json", state="CLOSED"))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "Next: nothing."


def test_a_closed_issue_is_done_even_when_the_board_cannot_be_read(fake_gh, repo, tmp_path):
    env = {**_story(tmp_path, "issue-closed.json", state="CLOSED"), **NO_BOARD}

    result = _next(repo, env=env)

    assert result.returncode == 0, result.stderr
    assert "Step: done" in result.stdout.splitlines()
    assert "#248 is closed." in result.stdout


def test_a_reviewed_story_answered_by_an_amend_is_ready(fake_gh, repo, tmp_path):
    comments = _comments(
        tmp_path,
        _raw(REVIEW, "2026-09-02T09:00:00Z"),
        _raw("Amended: dropped the second criterion", "2026-09-03T09:00:00Z"),
    )
    result = _next(repo, env={**REVIEWED, **_fieldvalues(tmp_path, None), **comments})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Step: ready", "Reviewed, nothing waiting."]


def test_a_review_nobody_ticked_is_not_feedback(fake_gh, repo, tmp_path):
    """An untouched review is the review itself, not an answer to it, so the story is ready to board."""
    comments = _comments(tmp_path, _raw(REVIEW, "2026-09-02T09:00:00Z"))
    result = _next(repo, env={**REVIEWED, **_fieldvalues(tmp_path, None), **comments})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Step: ready", "Reviewed, nothing waiting."]


def test_a_tick_before_any_amend_is_amended(fake_gh, repo, tmp_path):
    """The first tick has no amend to be later than, so it is measured against the review itself."""
    comments = _comments(tmp_path, _raw(REVIEW, "2026-09-02T09:00:00Z", "2026-09-04T11:00:00Z"))
    result = _next(repo, env={**REVIEWED, **_fieldvalues(tmp_path, None), **comments})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Step: amend", "Feedback waiting on the issue."]


def test_a_tick_since_the_last_amend_is_amended(fake_gh, repo, tmp_path):
    """Ticking a finding edits the review comment, so an edit after the last amend is feedback."""
    comments = _comments(
        tmp_path,
        _raw(REVIEW, "2026-09-02T09:00:00Z", "2026-09-04T11:00:00Z"),
        _raw("Amended: dropped the second criterion", "2026-09-03T09:00:00Z"),
    )
    result = _next(repo, env={**REVIEWED, **_fieldvalues(tmp_path, None), **comments})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Step: amend", "Feedback waiting on the issue."]


def test_a_reply_since_the_last_amend_is_amended(fake_gh, repo, tmp_path):
    comments = _comments(
        tmp_path,
        _raw(REVIEW, "2026-09-02T09:00:00Z"),
        _raw("Amended: dropped the second criterion", "2026-09-03T09:00:00Z"),
        _raw("Also cover the empty case.", "2026-09-04T09:00:00Z", login="mattjmoran"),
    )
    result = _next(repo, env={**REVIEWED, **_fieldvalues(tmp_path, None), **comments})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Step: amend", "Feedback waiting on the issue."]


# --- a fact that could not be read -------------------------------------------


def _stopped(result, fact: str) -> list[str]:
    """The printed lines, asserting the command stopped on `fact` and offered nothing else."""
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[-3:] == [
        "Step: stop",
        f"Cannot read {fact}; nothing decided.",
        "Next: /deckhand:next 248 when it can be read.",
    ]
    assert "## Instructions" not in lines
    return lines


def test_an_unreadable_repository_stops(no_real_gh, repo):
    """With gh unreachable nothing is known, and the command still exits 0."""
    result = _next(repo)

    lines = _stopped(result, "repository")
    assert "Traceback" not in result.stderr
    assert [line for line in lines if "unavailable" in line] == [lines[0]]
    assert lines[0].startswith("Repository: unavailable (")


def test_an_unreadable_board_stops_and_says_so_once(fake_gh, repo):
    result = _next(repo, env=NO_BOARD)

    lines = _stopped(result, "board")
    # The branch is derived from the board's Kind, so it costs one line, not two.
    assert [line for line in lines if "unavailable" in line] == [lines[0]]
    assert lines[0].startswith("Board: unavailable (")


def test_an_underivable_branch_stops(fake_gh, repo, tmp_path):
    env = {**_story(tmp_path, "issue-untitled.json", title="***"), **_fieldvalues(tmp_path, "Backlog")}

    result = _next(repo, env=env)

    lines = _stopped(result, "branch")
    assert lines[0].startswith("Branch: unavailable (")


def test_an_unreadable_pull_request_stops(fake_gh, repo, origin, branch, tmp_path):
    env = {**_fieldvalues(tmp_path, "In Progress"), "GH_PR_LIST_FAILS": "1"}

    result = _next(repo, env=env)

    lines = _stopped(result, "pull request")
    assert lines[0].startswith("Pull request: unavailable (")


def test_unreadable_comments_stop(fake_gh, repo, tmp_path):
    env = {**REVIEWED, **_fieldvalues(tmp_path, None), "GH_COMMENTS_FAILS": "1"}

    result = _next(repo, env=env)

    lines = _stopped(result, "comments")
    assert lines[0].startswith("Comments: unavailable (")


def test_a_story_in_a_column_next_cannot_act_on_stops_with_nothing_to_do(fake_gh, repo, origin, tmp_path):
    """Pending Review with no pull request is the board's business, so there is no command to offer."""
    result = _next(repo, env=_fieldvalues(tmp_path, "Pending Review"))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-3:] == [
        "Step: stop",
        "#248 is Pending Review with no open pull request; nothing decided.",
        "Next: nothing.",
    ]
