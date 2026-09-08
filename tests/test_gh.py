import os
import subprocess

import pytest

from deckhand import config, gh
from tests.conftest import FIXTURES


def test_config_exposes_defaults():
    assert config.DEFAULT_KINDS == ["feat", "fix", "chore", "refactor", "docs", "perf"]
    assert config.BODY_LIMIT == 65536


def test_linked_projects_reads_one_page_of_twenty(fake_gh, gh_calls, monkeypatch):
    monkeypatch.setenv("GH_LINKED_PROJECTS", str(FIXTURES / "linked-two.json"))
    assert gh.linked_projects("acme/widgets") == [
        gh.LinkedProject("acme", "Organization", 2, "Widgets"),
        gh.LinkedProject("acme", "Organization", 7, "Platform"),
    ]
    call = gh_calls()[-1]
    assert "projectsV2(first:20)" in call
    assert "-f owner=acme -f name=widgets" in call


def test_linked_projects_is_empty_for_an_unlinked_repository(fake_gh, monkeypatch):
    monkeypatch.setenv("GH_LINKED_PROJECTS", str(FIXTURES / "linked-none.json"))
    assert gh.linked_projects("acme/widgets") == []


def test_linked_projects_reports_a_missing_repository(fake_gh, tmp_path, monkeypatch):
    empty = tmp_path / "no-repo.json"
    empty.write_text('{"data":{"repository":null}}')
    monkeypatch.setenv("GH_LINKED_PROJECTS", str(empty))
    with pytest.raises(gh.GhError, match="no such repository: acme/widgets"):
        gh.linked_projects("acme/widgets")


def test_linked_projects_rejects_an_owner_type_it_cannot_query(fake_gh, tmp_path, monkeypatch):
    odd = tmp_path / "odd-owner.json"
    odd.write_text(
        '{"data":{"repository":{"projectsV2":{"nodes":'
        '[{"number":3,"title":"T","owner":{"__typename":"Enterprise","login":"acme"}}]}}}}'
    )
    monkeypatch.setenv("GH_LINKED_PROJECTS", str(odd))
    with pytest.raises(gh.GhError) as info:
        gh.linked_projects("acme/widgets")
    assert "#3" in str(info.value) and "Enterprise" in str(info.value)


def test_project_lookup_reports_a_project_with_no_owner_type(fake_gh, monkeypatch):
    monkeypatch.setenv("GH_PROJECT_OWNER_TYPE", "")
    with pytest.raises(gh.GhError) as info:
        gh.project_lookup("acme/widgets", 7)
    assert "#7" in str(info.value)


def test_linked_project_formats_as_owner_number_title():
    project = gh.LinkedProject("acme", "Organization", 2, "Tag filters\non the list")
    assert str(project) == "acme #2 Tag filters on the list"
    assert str(gh.LinkedProject("mjm", "User", 4, "")) == "mjm #4"


def test_owner_query_picks_the_root_field_from_the_owner_type():
    assert gh.owner_query("{OWNER_ROOT(login:$o)}", "User") == "{user(login:$o)}"
    assert gh.owner_query("{OWNER_ROOT(login:$o)}", "Organization") == "{organization(login:$o)}"
    with pytest.raises(gh.GhError, match="unknown project owner type"):
        gh.owner_query("{OWNER_ROOT(login:$o)}", "Enterprise")


def test_owner_query_refuses_a_query_with_no_owner_root_token():
    with pytest.raises(AssertionError, match="OWNER_ROOT"):
        gh.owner_query("{organization(login:$o)}", "Organization")


def test_repo_slug(fake_gh):
    assert gh.repo_slug() == "acme/widgets"


def test_project_id(fake_gh, settings):
    assert gh.project_id(settings) == "PVT_TEST"


def test_field_list_asks_the_configured_project_for_every_field(fake_gh, gh_calls, settings):
    names = [f["name"] for f in gh.field_list(settings)]

    assert names[:3] == ["Status", "Kind", "Story Points"]
    assert any("project field-list 2 --owner acme --format json" in c for c in gh_calls())


def test_field_returns_the_named_field(fake_gh, settings):
    result = gh.field(settings, "Story Points")
    assert result["id"] == "PVTF_POINTS"


def test_field_explains_a_missing_field(fake_gh, settings):
    with pytest.raises(gh.GhError) as info:
        gh.field(settings, "Nope")
    assert "no such project field: Nope" in str(info.value)


def test_item_id_picks_the_item_on_the_configured_project(fake_gh, settings):
    assert gh.item_id(settings, "acme/widgets", 248) == "PVTI_TEST_248"


def test_item_id_is_empty_off_the_board(fake_gh, tmp_path, monkeypatch, settings):
    none = tmp_path / "none.json"
    none.write_text('{"data":{"repository":{"issue":{"projectItems":{"nodes":[]}}}}}')
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(none))
    assert gh.item_id(settings, "acme/widgets", 248) is None


def test_item_id_sends_owner_repo_and_number(fake_gh, settings, gh_calls):
    gh.item_id(settings, "Other-Org/other-repo", 248)
    assert "-f o=Other-Org -f r=other-repo -F n=248" in gh_calls()[-1]


def test_issue_id_requests_the_issue_path(fake_gh, gh_calls):
    gh.issue_id("acme/gadgets", 246)
    assert "api repos/acme/gadgets/issues/246" in gh_calls()[-1]


def test_issue_id_returns_the_database_id(fake_gh):
    assert gh.issue_id("acme/widgets", 246) == 5099965156


def test_project_id_passes_the_owner(fake_gh, settings, gh_calls):
    gh.project_id(settings)
    assert "project view 2 --owner acme" in gh_calls()[-1]


def test_a_failing_repo_slug_stops_item_id_and_issue_id(fake_gh, tmp_path, monkeypatch, settings, gh_calls):
    broken = tmp_path / "bin"
    broken.mkdir()
    (broken / "gh").write_text(
        '#!/bin/sh\nif [ "$1 $2" = "repo view" ]; then echo "gh: repo view failed" >&2; exit 1; fi\necho "{}"\n'
    )
    (broken / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{broken}{os.pathsep}{os.environ['PATH']}")

    with pytest.raises(gh.GhError):
        gh.item_id(settings, gh.repo_slug(), 246)
    with pytest.raises(gh.GhError):
        gh.issue_id(gh.repo_slug(), 246)

    assert gh_calls() == []


def test_field_reports_a_gh_failure_not_a_missing_field(fake_gh, tmp_path, monkeypatch, settings):
    broken = tmp_path / "bin"
    broken.mkdir()
    (broken / "gh").write_text("#!/bin/sh\necho 'gh: not authenticated' >&2\nexit 1\n")
    (broken / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{broken}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(gh.GhError) as info:
        gh.field(settings, "Story Points")
    assert "not authenticated" in str(info.value) and "no such project field" not in str(info.value)


def test_graphql_paginate_returns_one_page_per_fixture(fake_gh, tmp_path, monkeypatch):
    page1 = tmp_path / "page1.json"
    page2 = tmp_path / "page2.json"
    page1.write_text('{"data":{"n":1}}')
    page2.write_text('{"data":{"n":2}}')
    monkeypatch.setenv("GH_PAGES", f"{page1}:{page2}")
    pages = gh.graphql("query{x}", paginate=True)
    assert pages == [{"data": {"n": 1}}, {"data": {"n": 2}}]


def test_paginated_flattens_array_pages(fake_gh, tmp_path, monkeypatch):
    page1 = tmp_path / "page1.json"
    page2 = tmp_path / "page2.json"
    page1.write_text('[{"id": 1}, {"id": 2}]')
    page2.write_text('[{"id": 3}]')
    monkeypatch.setenv("GH_PAGES", f"{page1}:{page2}")
    items = gh.paginated("repos/acme/widgets/issues/1/dependencies/blocked_by")
    assert items == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_typed_variables_render_bool_and_none(fake_gh, gh_calls):
    gh.graphql("query{x}", {"flag": True, "nothing": None})
    assert "-F flag=true" in gh_calls()[-1]
    assert "-F nothing=null" in gh_calls()[-1]


def test_missing_gh_binary_is_reported(tmp_path, monkeypatch):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(gh.GhError) as info:
        gh.run("repo", "view")
    assert "not installed" in str(info.value)


def test_split_repo_rejects_a_bare_name():
    with pytest.raises(gh.GhError) as info:
        gh.split_repo("widgets")
    assert "OWNER/REPO" in str(info.value)


def test_repo_slug_is_cached_across_calls(fake_gh, settings, gh_calls):
    gh.field(settings, "Story Points")
    gh.project_id(settings)
    gh.item_id(settings, gh.repo_slug(), 248)
    repo_view_calls = [line for line in gh_calls() if line.startswith("repo view")]
    assert len(repo_view_calls) == 1


def test_fake_gh_refuses_slurp_with_jq(fake_gh):
    result = subprocess.run(
        ["gh", "api", "repos/acme/widgets/issues", "--paginate", "--slurp", "--jq", ".[]"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "not supported with --jq" in result.stderr


def test_fake_gh_refuses_slurp_without_paginate(fake_gh):
    result = subprocess.run(
        ["gh", "api", "repos/acme/widgets/issues", "--slurp"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "--paginate required when passing --slurp" in result.stderr


def test_paginated_raises_on_an_object_page(fake_gh, tmp_path, monkeypatch):
    page = tmp_path / "page1.json"
    page.write_text('{"not": "a list"}')
    monkeypatch.setenv("GH_PAGES", str(page))
    with pytest.raises(gh.GhError) as info:
        gh.paginated("repos/acme/widgets/issues/1/dependencies/blocked_by")
    assert "expected a list page" in str(info.value)
    assert "repos/acme/widgets/issues/1/dependencies/blocked_by" in str(info.value)


def test_paginated_names_gh_api_on_an_object_page_with_no_args(monkeypatch):
    # A zero-arg call has no endpoint to name; the guard must not raise IndexError reaching for args[0].
    monkeypatch.setattr(gh, "run", lambda *args, **kwargs: '{"not": "a list"}')
    with pytest.raises(gh.GhError) as info:
        gh.paginated()
    assert "expected a list page from gh api --paginate --slurp, got an object" in str(info.value)


@pytest.mark.parametrize("repo", ["acme/a/b", "acme/.", "acme/.."])
def test_split_repo_rejects_an_unsafe_name(repo):
    with pytest.raises(gh.GhError, match="expected OWNER/REPO"):
        gh.split_repo(repo)
