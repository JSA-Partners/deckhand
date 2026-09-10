from __future__ import annotations

from pathlib import Path

import pytest

from deckhand import gh, issue

REPO = "acme/widgets"
# A backtick, a shell variable, a quote, and a newline: everything a command line would mangle.
TRICKY_BODY = '### Notes\n\nRun `deckhand ready $HOME` and say "ok".\nThen stop.\n'


@pytest.fixture
def body_copy(fake_gh, tmp_path, monkeypatch) -> Path:
    """Have the fake append every body file it is handed; the only way a test sees what reached gh."""
    path = tmp_path / "bodies"
    monkeypatch.setenv("GH_BODY_FILE_COPY", str(path))
    return path


def _body_file_path(call: str) -> Path:
    return Path(call.split("--body-file ")[1])


# --- view -------------------------------------------------------------------


def test_view_parses_comments(fake_gh, gh_calls):
    result = issue.view(REPO, 248)
    assert result.number == 248
    assert result.state == "OPEN"
    assert result.url == "https://github.com/acme/widgets/issues/248"
    assert result.title.startswith("Guest users")
    assert "### Story" in result.body
    assert [(c.author, c.created_at) for c in result.comments] == [
        ("mattjmoran", "2026-09-01T10:00:00Z"),
        ("arjan", "2026-09-01T12:00:00Z"),
    ]
    assert result.comments[1].body == "Approved."
    assert gh_calls() == ["issue view 248 --repo acme/widgets --json number,title,body,url,state,comments"]


# --- sibling ----------------------------------------------------------------


def test_sibling_asks_for_the_state_and_the_body(fake_gh, gh_calls):
    state, body = issue.sibling(REPO, 57)

    assert gh_calls() == ["issue view 57 --repo acme/widgets --json state,body"]
    assert state == "OPEN"
    assert body.startswith("### Story")


def test_sibling_rejects_a_malformed_repo(fake_gh, gh_calls):
    with pytest.raises(gh.GhError, match="expected OWNER/REPO"):
        issue.sibling("widgets", 57)
    assert gh_calls() == []


# --- create -----------------------------------------------------------------


def test_create_returns_number_and_url(fake_gh, gh_calls, body_copy, monkeypatch):
    monkeypatch.setenv("GH_NEW_ISSUE", "251")
    number, url = issue.create(REPO, "A title", TRICKY_BODY)
    assert (number, url) == (251, "https://github.com/acme/widgets/issues/251")
    call = gh_calls()[-1]
    assert call.startswith("issue create --repo acme/widgets --title A title --body-file ")
    assert body_copy.read_text() == f"--- issue create\n{TRICKY_BODY}"
    assert not _body_file_path(call).exists()


def test_create_rejects_unexpected_output(fake_gh, monkeypatch):
    monkeypatch.setenv("GH_NEW_ISSUE", "notanumber")
    with pytest.raises(gh.GhError, match="unexpected output from gh issue create"):
        issue.create(REPO, "A title", TRICKY_BODY)


# --- update_body ------------------------------------------------------------


def test_update_body_uses_a_body_file(fake_gh, gh_calls, body_copy):
    issue.update_body(REPO, 248, TRICKY_BODY)
    call = gh_calls()[-1]
    assert call.startswith("issue edit 248 --repo acme/widgets --body-file ")
    assert "deckhand ready" not in call  # the body travels in the file, never on the command line
    assert body_copy.read_text() == f"--- issue edit\n{TRICKY_BODY}"
    assert not _body_file_path(call).exists()  # and the file goes away after the call


def test_a_large_body_reaches_gh_intact(fake_gh, body_copy):
    body = "### Plan\n\n" + "- [ ] a step with a `backtick`\n" * 2500
    assert len(body) > 70_000
    issue.update_body(REPO, 248, body)
    assert body_copy.read_text() == f"--- issue edit\n{body}"


# --- set_title --------------------------------------------------------------


def test_set_title_edits_the_title_alone(fake_gh, gh_calls):
    issue.set_title(REPO, 57, "Grant store")

    assert gh_calls() == ["issue edit 57 --repo acme/widgets --title Grant store"]


def test_set_title_rejects_a_malformed_repo(fake_gh, gh_calls):
    with pytest.raises(gh.GhError):
        issue.set_title("widgets", 57, "Grant store")
    assert gh_calls() == []


# --- assign -----------------------------------------------------------------


def test_assign_adds_the_running_login_by_default(fake_gh, gh_calls):
    issue.assign(REPO, 248)

    assert gh_calls() == ["issue edit 248 --repo acme/widgets --add-assignee @me"]


def test_assign_takes_a_named_login(fake_gh, gh_calls):
    issue.assign(REPO, 248, "mjm")

    assert gh_calls() == ["issue edit 248 --repo acme/widgets --add-assignee mjm"]


def test_assign_rejects_a_malformed_repo(fake_gh, gh_calls):
    with pytest.raises(gh.GhError):
        issue.assign("widgets", 248)
    assert gh_calls() == []


# --- close ------------------------------------------------------------------


def test_close_closes_the_issue(fake_gh, gh_calls):
    issue.close(REPO, 248)
    assert gh_calls() == ["issue close 248 --repo acme/widgets"]


# --- comment ----------------------------------------------------------------


def test_comment_returns_the_url(fake_gh, gh_calls, body_copy, monkeypatch):
    monkeypatch.setenv("GH_NEW_COMMENT", "88")
    url = issue.comment(REPO, 248, TRICKY_BODY)
    assert url == "https://github.com/acme/widgets/issues/248#issuecomment-88"
    assert gh_calls()[-1].startswith("issue comment 248 --repo acme/widgets --body-file ")
    assert body_copy.read_text() == f"--- issue comment\n{TRICKY_BODY}"


def test_comment_failure_raises_gh_error(fake_gh, monkeypatch):
    monkeypatch.setenv("GH_ISSUE_COMMENT_FAILS", "1")
    with pytest.raises(gh.GhError, match="could not add comment"):
        issue.comment(REPO, 248, "## Review\n")


def test_the_temp_file_is_removed_on_the_error_path(fake_gh, gh_calls, body_copy, monkeypatch):
    monkeypatch.setenv("GH_ISSUE_COMMENT_FAILS", "1")
    with pytest.raises(gh.GhError):
        issue.comment(REPO, 248, TRICKY_BODY)
    assert body_copy.read_text() == f"--- issue comment\n{TRICKY_BODY}"  # gh did read the file
    assert not _body_file_path(gh_calls()[-1]).exists()


# --- add_dependency ---------------------------------------------------------


def test_add_dependency_posts_the_blocking_issue_id(fake_gh, gh_calls):
    issue.add_dependency(REPO, 248, 240)
    assert gh_calls() == [
        "api repos/acme/widgets/issues/240",
        "api -X POST repos/acme/widgets/issues/248/dependencies/blocked_by -F issue_id=5099965156",
    ]


# --- blockers ---------------------------------------------------------------


def test_blockers_returns_nothing_when_unblocked(fake_gh):
    assert issue.blockers(REPO, 248) == []


def test_blockers_lists_open_blockers_only(fake_gh, monkeypatch):
    monkeypatch.setenv(
        "GH_BLOCKED_BY",
        '[{"number":240,"title":"Grant store","state":"open"},{"number":230,"title":"Old","state":"closed"}]',
    )
    assert issue.blockers(REPO, 248) == [(240, "Grant store")]


def test_blockers_accepts_repo(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_BLOCKED_BY", '[{"number":9,"title":"X","state":"open"}]')
    assert issue.blockers("acme/gadgets", 248) == [(9, "X")]
    assert gh_calls() == ["api repos/acme/gadgets/issues/248/dependencies/blocked_by --paginate --slurp"]


# --- blocking ---------------------------------------------------------------


def test_blocking_returns_nothing_when_the_story_blocks_no_one(fake_gh):
    assert issue.blocking(REPO, 248) == []


def test_blocking_lists_open_issues_only_in_ascending_order(fake_gh, monkeypatch):
    monkeypatch.setenv(
        "GH_BLOCKING",
        '[{"number":4,"state":"open"},{"number":9,"state":"closed"},{"number":3,"state":"open"}]',
    )
    assert issue.blocking(REPO, 248) == [3, 4]


def test_blocking_accepts_repo(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_BLOCKING", '[{"number":9,"state":"open"}]')
    assert issue.blocking("acme/gadgets", 248) == [9]
    assert gh_calls() == ["api repos/acme/gadgets/issues/248/dependencies/blocking --paginate --slurp"]


# --- pull_request -----------------------------------------------------------


def test_pull_request_returns_the_open_ones_url(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_PR_EXISTS", "1")

    assert issue.pull_request(REPO, "feat/248-guests") == "https://github.com/acme/widgets/pull/1000"
    assert gh_calls() == ["pr list --repo acme/widgets --head feat/248-guests --state open --json url"]


def test_pull_request_is_none_when_the_branch_has_none(fake_gh):
    assert issue.pull_request(REPO, "feat/248-guests") is None


def test_pull_request_survives_an_answer_it_did_not_expect(fake_gh, monkeypatch):
    """gh answers a list here, and a shape that is not one must read as no pull request."""
    monkeypatch.setenv("GH_PR_LIST_BODY", '{"url": "no"}')

    assert issue.pull_request(REPO, "feat/248-guests") is None


def test_pull_request_rejects_a_malformed_repo(fake_gh, gh_calls):
    with pytest.raises(gh.GhError):
        issue.pull_request("widgets", "feat/248-guests")
    assert gh_calls() == []


# --- pull_request_state ----------------------------------------------------


PR_URL = "https://github.com/acme/widgets/pull/1000"


def test_pull_request_state_returns_the_url_while_it_is_open(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_PR_STATE", "OPEN")

    assert issue.pull_request_state(REPO, PR_URL) == PR_URL
    assert gh_calls() == [f"pr view {PR_URL} --repo acme/widgets --json state,url"]


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_pull_request_state_is_none_once_it_is_not_open(fake_gh, monkeypatch, state):
    monkeypatch.setenv("GH_PR_STATE", state)

    assert issue.pull_request_state(REPO, PR_URL) is None


def test_pull_request_state_fails_when_the_pull_request_cannot_be_read(fake_gh):
    with pytest.raises(gh.GhError):
        issue.pull_request_state(REPO, PR_URL)


def test_pull_request_state_rejects_a_malformed_repo(fake_gh, gh_calls):
    with pytest.raises(gh.GhError):
        issue.pull_request_state("widgets", PR_URL)
    assert gh_calls() == []
