from __future__ import annotations

import json
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
    assert gh_calls() == ["issue view 248 --repo acme/widgets --json number,title,body,url,state,comments,labels"]


def test_view_carries_the_labels(fake_gh):
    """A board holds issues that are not the process's business, and only a label can say which are."""
    assert issue.view(REPO, 248).labels == ("deckhand",)


# --- sibling ----------------------------------------------------------------


def test_open_titles_lists_the_open_stories_of_the_repository(fake_gh, gh_calls, tmp_path, monkeypatch):
    listing = tmp_path / "titles.json"
    listing.write_text(json.dumps([{"number": 321, "title": "Compare  times"}, {"number": 12, "title": "Share"}]))
    monkeypatch.setenv("GH_ISSUE_LIST_FILE", str(listing))

    assert issue.open_titles(REPO) == [(321, "Compare times"), (12, "Share")]
    assert gh_calls() == [
        "issue list --repo acme/widgets --state open --label deckhand --limit 200 --json number,title"
    ]


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


def test_create_labels_the_issue(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_LABELS", json.dumps([{"name": "deckhand"}]))
    issue.create(REPO, "A title", TRICKY_BODY)
    (call,) = [c for c in gh_calls() if c.startswith("issue create")]
    assert " --label deckhand" in call


def test_create_makes_the_label_where_it_is_missing(fake_gh, gh_calls):
    issue.create(REPO, "A title", TRICKY_BODY)
    calls = gh_calls()
    assert calls[:2] == [
        "label list --repo acme/widgets --limit 200 --json name",
        "label create deckhand --repo acme/widgets --color 5319e7 --description A story deckhand runs",
    ]
    assert calls[2].startswith("issue create --repo acme/widgets --title A title")


def test_create_leaves_a_label_that_is_there(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_LABELS", json.dumps([{"name": "deckhand"}]))
    issue.create(REPO, "A title", TRICKY_BODY)
    assert not [c for c in gh_calls() if c.startswith("label create")]


def test_the_label_is_checked_once_per_repository(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_LABELS", json.dumps([{"name": "deckhand"}]))
    issue.create(REPO, "A title", TRICKY_BODY)
    issue.create(REPO, "Another title", TRICKY_BODY)
    assert len([c for c in gh_calls() if c.startswith("label list")]) == 1


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
    issue.add_dependency(REPO, 248, (REPO, 240))
    assert gh_calls() == [
        "api repos/acme/widgets/issues/240",
        "api -X POST repos/acme/widgets/issues/248/dependencies/blocked_by -F issue_id=5099965156",
    ]


# --- blockers ---------------------------------------------------------------


def test_blockers_returns_nothing_when_unblocked(fake_gh):
    assert issue.blockers(REPO, 248) == []


def test_blockers_lists_open_blockers_with_their_repository(fake_gh, monkeypatch):
    monkeypatch.setenv(
        "GH_BLOCKED_BY",
        '[{"number":240,"title":"Grant  store","state":"open","repository":{"full_name":"acme/widgets"}},'
        '{"number":9,"title":"Endpoint","state":"open","repository":{"full_name":"acme/gadgets"}},'
        '{"number":230,"title":"Old","state":"closed","repository":{"full_name":"acme/widgets"}}]',
    )
    assert issue.blockers(REPO, 248) == [("acme/widgets", 240, "Grant store"), ("acme/gadgets", 9, "Endpoint")]


def test_blockers_takes_the_story_repository_for_an_item_without_one(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_BLOCKED_BY", '[{"number":9,"title":"X","state":"open"}]')
    assert issue.blockers("acme/gadgets", 248) == [("acme/gadgets", 9, "X")]
    assert gh_calls() == ["api repos/acme/gadgets/issues/248/dependencies/blocked_by --paginate --slurp"]


def test_add_dependency_looks_the_blocker_up_in_its_own_repository(fake_gh, gh_calls):
    issue.add_dependency(REPO, 248, ("acme/gadgets", 9))

    assert gh_calls() == [
        "api repos/acme/gadgets/issues/9",
        "api -X POST repos/acme/widgets/issues/248/dependencies/blocked_by -F issue_id=5099965156",
    ]


# --- blocking ---------------------------------------------------------------


def test_blocking_returns_nothing_when_the_story_blocks_no_one(fake_gh):
    assert issue.blocking(REPO, 248) == []


def test_blocking_lists_open_issues_only_in_ascending_order(fake_gh, monkeypatch):
    monkeypatch.setenv(
        "GH_BLOCKING",
        '[{"number":4,"state":"open"},{"number":9,"state":"closed"},{"number":3,"state":"open"}]',
    )
    assert [number for _, number, _ in issue.blocking(REPO, 248)] == [3, 4]


def test_blocking_names_the_repository_of_an_issue_elsewhere(fake_gh, monkeypatch):
    waiting = [
        {"number": 9, "title": "Waits  on  it", "state": "open", "repository": {"full_name": "acme/other"}},
        {"number": 10, "title": "Done", "state": "closed", "repository": {"full_name": REPO}},
    ]
    monkeypatch.setenv("GH_BLOCKING", json.dumps(waiting))

    assert issue.blocking(REPO, 248) == [("acme/other", 9, "Waits on it")]


def test_blocking_accepts_repo(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_BLOCKING", '[{"number":9,"state":"open"}]')
    assert [number for _, number, _ in issue.blocking("acme/gadgets", 248)] == [9]
    assert gh_calls() == ["api repos/acme/gadgets/issues/248/dependencies/blocking --paginate --slurp"]


# --- pull_request -----------------------------------------------------------


def test_pull_request_returns_the_open_ones_url_and_state(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_PR_EXISTS", "1")

    assert issue.pull_request(REPO, "feat/248-guests") == ("https://github.com/acme/widgets/pull/1000", "OPEN")
    assert gh_calls() == ["pr list --repo acme/widgets --head feat/248-guests --state all --json url,state"]


def test_pull_request_prefers_the_open_one_to_a_merged_one(fake_gh, monkeypatch):
    listed = [
        {"url": "https://github.com/acme/widgets/pull/9", "state": "MERGED"},
        {"url": "https://github.com/acme/widgets/pull/12", "state": "OPEN"},
    ]
    monkeypatch.setenv("GH_PR_LIST_BODY", json.dumps(listed))

    assert issue.pull_request(REPO, "feat/248-guests") == ("https://github.com/acme/widgets/pull/12", "OPEN")


def test_pull_request_finds_a_merged_one_and_passes_over_a_closed_one(fake_gh, monkeypatch):
    listed = [
        {"url": "https://github.com/acme/widgets/pull/7", "state": "CLOSED"},
        {"url": "https://github.com/acme/widgets/pull/9", "state": "MERGED"},
    ]
    monkeypatch.setenv("GH_PR_LIST_BODY", json.dumps(listed))

    assert issue.pull_request(REPO, "feat/248-guests") == ("https://github.com/acme/widgets/pull/9", "MERGED")


def test_pull_request_is_none_when_the_branch_has_only_a_closed_one(fake_gh, monkeypatch):
    """Closed without merging, it neither opens the story's review nor says the story landed."""
    listed = [{"url": "https://github.com/acme/widgets/pull/7", "state": "CLOSED"}]
    monkeypatch.setenv("GH_PR_LIST_BODY", json.dumps(listed))

    assert issue.pull_request(REPO, "feat/248-guests") is None


def test_pull_request_is_none_when_the_branch_has_none(fake_gh):
    assert issue.pull_request(REPO, "feat/248-guests") is None


def test_pull_request_survives_an_answer_it_did_not_expect(fake_gh, monkeypatch):
    """gh answers a list here, and a shape that is not one must read as no pull request."""
    monkeypatch.setenv("GH_PR_LIST_BODY", '{"url": "no"}')

    assert issue.pull_request(REPO, "feat/248-guests") is None


def test_merged_pull_requests_reads_titles_and_bodies(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_PR_LIST_BODY", '[{"title": "fix: keep a grant", "body": "Keeps it."}]')

    assert issue.merged_pull_requests(REPO, 3) == [("fix: keep a grant", "Keeps it.")]
    assert gh_calls() == ["pr list --repo acme/widgets --state merged --limit 3 --json title,body"]


def test_merged_pull_requests_survives_an_answer_it_did_not_expect(fake_gh, monkeypatch):
    monkeypatch.setenv("GH_PR_LIST_BODY", '{"title": "no"}')

    assert issue.merged_pull_requests(REPO, 3) == []


def test_pull_request_rejects_a_malformed_repo(fake_gh, gh_calls):
    with pytest.raises(gh.GhError):
        issue.pull_request("widgets", "feat/248-guests")
    assert gh_calls() == []


# --- pull_request_state ----------------------------------------------------


PR_URL = "https://github.com/acme/widgets/pull/1000"


# --- pull_request_for --------------------------------------------------------


def _pr_node(number=1000, state="OPEN", merged=False, merge_state="CLEAN", contexts=None, files=()):
    return {
        "number": number,
        "url": f"https://github.com/acme/widgets/pull/{number}",
        "state": state,
        "merged": merged,
        "mergeStateStatus": merge_state,
        "files": {"nodes": [{"path": path} for path in files]},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": contexts or []}}}}]},
    }


def _pr_reference_fixture(tmp_path, *nodes):
    body = json.dumps({"data": {"repository": {"issue": {"closedByPullRequestsReferences": {"nodes": list(nodes)}}}}})
    path = tmp_path / "pr-reference.json"
    path.write_text(body)
    return path


def test_pull_request_for_reads_an_open_pull_request(fake_gh, gh_calls, tmp_path, monkeypatch):
    path = _pr_reference_fixture(tmp_path, _pr_node(number=1000, state="OPEN", merged=False))
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    result = issue.pull_request_for(REPO, 248)

    assert result.state == "OPEN"
    assert result.merged is False
    assert result.number == 1000
    assert result.url == "https://github.com/acme/widgets/pull/1000"
    call = gh_calls()[-1]
    assert "closedByPullRequestsReferences" in call
    assert "includeClosedPrs:true" in call
    assert "-f owner=acme -f name=widgets -F number=248" in call


def test_pull_request_for_reads_a_merged_pull_request(fake_gh, tmp_path, monkeypatch):
    path = _pr_reference_fixture(tmp_path, _pr_node(state="MERGED", merged=True))
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    result = issue.pull_request_for(REPO, 248)

    assert result.state == "MERGED"
    assert result.merged is True


def test_pull_request_for_is_none_without_a_linked_pull_request(fake_gh, tmp_path, monkeypatch):
    path = _pr_reference_fixture(tmp_path)
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    assert issue.pull_request_for(REPO, 248) is None


def test_pull_request_for_prefers_the_open_node_over_an_older_closed_one(fake_gh, tmp_path, monkeypatch):
    path = _pr_reference_fixture(
        tmp_path,
        _pr_node(number=900, state="CLOSED", merged=False),
        _pr_node(number=1000, state="OPEN", merged=False),
    )
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    result = issue.pull_request_for(REPO, 248)

    assert result.number == 1000
    assert result.state == "OPEN"


def test_pull_request_for_takes_the_first_node_when_none_is_open(fake_gh, tmp_path, monkeypatch):
    path = _pr_reference_fixture(
        tmp_path,
        _pr_node(number=900, state="CLOSED", merged=False),
        _pr_node(number=1000, state="MERGED", merged=True),
    )
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    result = issue.pull_request_for(REPO, 248)

    assert result.number == 900


@pytest.mark.parametrize("merge_state", ["BEHIND", "DIRTY"])
def test_pull_request_for_is_behind_when_main_moved_on_or_the_merge_would_conflict(
    fake_gh, tmp_path, monkeypatch, merge_state
):
    path = _pr_reference_fixture(tmp_path, _pr_node(merge_state=merge_state))
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    assert issue.pull_request_for(REPO, 248).behind is True


def test_pull_request_for_is_not_behind_when_clean(fake_gh, tmp_path, monkeypatch):
    path = _pr_reference_fixture(tmp_path, _pr_node(merge_state="CLEAN"))
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    assert issue.pull_request_for(REPO, 248).behind is False


def test_pull_request_for_is_neither_when_github_has_not_worked_it_out(fake_gh, gh_calls, tmp_path, monkeypatch):
    """UNKNOWN is computed lazily; it must never be read as a settled False."""
    path = _pr_reference_fixture(tmp_path, _pr_node(merge_state="UNKNOWN"))
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    result = issue.pull_request_for(REPO, 248)

    assert result.behind is None
    assert len([call for call in gh_calls() if call.startswith("api graphql")]) == 2


def test_pull_request_for_asks_once_when_the_merge_state_is_already_settled(fake_gh, gh_calls, tmp_path, monkeypatch):
    path = _pr_reference_fixture(tmp_path, _pr_node(merge_state="CLEAN"))
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    issue.pull_request_for(REPO, 248)

    assert len([call for call in gh_calls() if call.startswith("api graphql")]) == 1


def test_pull_request_for_names_the_failed_check_and_counts_the_running_one(fake_gh, tmp_path, monkeypatch):
    contexts = [
        {"name": "Unit tests", "conclusion": "FAILURE"},
        {"name": "Lint", "conclusion": "SUCCESS"},
        {"context": "ci/legacy", "state": "PENDING"},
    ]
    path = _pr_reference_fixture(tmp_path, _pr_node(contexts=contexts))
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    result = issue.pull_request_for(REPO, 248)

    assert result.failed_checks == ("Unit tests",)
    assert result.pending_checks == 1


def test_pull_request_for_reads_the_files_it_changes(fake_gh, gh_calls, tmp_path, monkeypatch):
    path = _pr_reference_fixture(tmp_path, _pr_node(files=("api/auth.go", "api/auth_test.go")))
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    result = issue.pull_request_for(REPO, 248)

    assert result.files == ("api/auth.go", "api/auth_test.go")
    assert "files(first:100){ nodes{ path } }" in gh_calls()[-1]


def test_pull_request_for_changes_no_files_when_the_node_has_none(fake_gh, tmp_path, monkeypatch):
    node = _pr_node()
    del node["files"]
    path = _pr_reference_fixture(tmp_path, node)
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(path))

    assert issue.pull_request_for(REPO, 248).files == ()


# --- a dependency GitHub already holds ----------------------------------------

EDGE = json.dumps([{"number": 7, "title": "The blocker", "state": "open", "repository": {"full_name": REPO}}])


def test_a_dependency_already_recorded_is_not_an_error(fake_gh, monkeypatch):
    """The state the caller asked for is the state there is, so the write is done."""
    monkeypatch.setenv("GH_DEPENDENCY_TAKEN", "1")
    monkeypatch.setenv("GH_BLOCKED_BY", EDGE)

    issue.add_dependency(REPO, 8, blocked_by=(REPO, 7))


def test_a_dependency_that_failed_for_another_reason_raises(fake_gh, monkeypatch):
    monkeypatch.setenv("GH_DEPENDENCY_TAKEN", "1")
    monkeypatch.setenv("GH_BLOCKED_BY", "[]")

    with pytest.raises(gh.GhError):
        issue.add_dependency(REPO, 8, blocked_by=(REPO, 7))
