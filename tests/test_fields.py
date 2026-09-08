from __future__ import annotations

import pytest

from deckhand import config, fields, gh
from tests.conftest import FIXTURES

REPO = "acme/widgets"


def _off_board(tmp_path, monkeypatch) -> None:
    """An issue with no item on the board, so every read answers None and every write refuses."""
    none = tmp_path / "none.json"
    none.write_text('{"data":{"repository":{"issue":{"projectItems":{"nodes":[]}}}}}')
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(none))


def _user_owned_item(tmp_path, monkeypatch) -> None:
    """A board item on the user-owned project #4 the linked-user fixture names."""
    item = tmp_path / "user-item.json"
    item.write_text(
        '{"data":{"repository":{"issue":{"projectItems":{"nodes":[{"id":"PVTI_TEST_248","project":{"number":4}}]}}}}}'
    )
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(item))
    monkeypatch.setenv("GH_LINKED_PROJECTS", str(FIXTURES / "linked-user.json"))


# --- get_field ----------------------------------------------------------


def test_get_field_reads_a_single_select_value(fake_gh, settings):
    assert fields.get_field(settings, REPO, 248, "Kind") == "feat"


def test_get_field_reads_a_number(fake_gh, settings):
    assert fields.get_field(settings, REPO, 248, "Story Points") == "3"


def test_get_field_answers_none_for_an_unset_field(fake_gh, settings):
    assert fields.get_field(settings, REPO, 248, "Actual") is None


def test_get_field_answers_none_when_the_issue_is_not_on_the_board(fake_gh, tmp_path, monkeypatch, settings):
    _off_board(tmp_path, monkeypatch)

    assert fields.get_field(settings, REPO, 248, "Kind") is None


def test_get_field_requires_a_linked_project_even_off_board(fake_gh, tmp_path, monkeypatch):
    _off_board(tmp_path, monkeypatch)
    monkeypatch.setenv("GH_LINKED_PROJECTS", str(FIXTURES / "linked-none.json"))

    with pytest.raises(config.NoProject, match="no project is linked to acme/widgets"):
        fields.get_field(config.load(tmp_path), REPO, 248, "Kind")


def test_get_field_reads_a_user_owned_project(fake_gh, tmp_path, monkeypatch):
    _user_owned_item(tmp_path, monkeypatch)

    assert fields.get_field(config.load(tmp_path), REPO, 248, "Kind") == "feat"


# --- get_fields ---------------------------------------------------------


def test_get_fields_answers_every_name_from_one_pair_of_queries(fake_gh, gh_calls, settings):
    values = fields.get_fields(settings, REPO, 248, ("Kind", "Story Points", "Actual"))

    assert values == {"Kind": "feat", "Story Points": "3", "Actual": None}
    graphql = [c for c in gh_calls() if c.startswith("api graphql")]
    assert len(graphql) == 2
    assert "projectItems(first" in graphql[0] and "node(id:" in graphql[1]


def test_get_fields_answers_none_for_every_name_off_the_board(fake_gh, tmp_path, monkeypatch, settings):
    _off_board(tmp_path, monkeypatch)

    assert fields.get_fields(settings, REPO, 248, ("Kind", "Status")) == {
        "Kind": None,
        "Status": None,
    }


# --- set_field ----------------------------------------------------------


def test_set_field_maps_a_single_select_option_name_to_its_id(fake_gh, gh_calls, settings):
    assert fields.set_field(settings, REPO, 248, "Kind", "fix") == "Kind=fix"
    assert any(
        "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTSSF_KIND "
        "--single-select-option-id opt_fix" in line
        for line in gh_calls()
    )


def test_set_field_sends_numbers_with_number(fake_gh, gh_calls, settings):
    fields.set_field(settings, REPO, 248, "Actual", "5")

    assert any(
        "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTF_ACTUAL --number 5" in line
        for line in gh_calls()
    )


def test_set_field_sends_dates_with_date(fake_gh, gh_calls, settings):
    # The flag comes from the value's shape, not the field's: any field takes a date this way.
    fields.set_field(settings, REPO, 248, "Closed", "2026-09-02")

    assert any(
        "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTF_CLOSED --date 2026-09-02" in line
        for line in gh_calls()
    )


def test_set_field_sends_text_with_text(fake_gh, gh_calls, settings):
    fields.set_field(settings, REPO, 248, "Actual", "tbd")

    assert any(
        "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTF_ACTUAL --text tbd" in line
        for line in gh_calls()
    )


def test_set_field_rejects_an_unknown_option(fake_gh, gh_calls, settings):
    with pytest.raises(gh.GhError, match="no option 'feature'"):
        fields.set_field(settings, REPO, 248, "Kind", "feature")

    assert not any("item-edit" in line for line in gh_calls())
    assert not any("project view" in line for line in gh_calls())


def test_set_field_names_the_linked_user_as_the_project_owner(fake_gh, gh_calls, tmp_path, monkeypatch):
    _user_owned_item(tmp_path, monkeypatch)

    fields.set_field(config.load(tmp_path), REPO, 248, "Kind", "fix")

    assert any("project field-list 4 --owner mjm" in call for call in gh_calls())
    assert any("project view 4 --owner mjm" in call for call in gh_calls())


def test_set_field_raises_not_on_board_specifically_off_the_board(fake_gh, gh_calls, tmp_path, monkeypatch, settings):
    # A typed exception, not a bare gh.GhError: a caller needs to be able to catch exactly
    # "no board item" without also swallowing an unrelated gh failure that happens to mention it.
    _off_board(tmp_path, monkeypatch)

    with pytest.raises(fields.NotOnBoard) as info:
        fields.set_field(settings, REPO, 248, "Kind", "fix")

    assert isinstance(info.value, gh.GhError)
    assert "not on project" in str(info.value)
    assert not any("item-edit" in line for line in gh_calls())


def test_set_field_resolves_the_project_before_it_reads_the_board(fake_gh, gh_calls, tmp_path, monkeypatch):
    # The NotOnBoard message names the project owner, so the link has to be readable before the
    # board check; resolving after it would turn an off-board no-op into a bare gh failure.
    _off_board(tmp_path, monkeypatch)
    monkeypatch.setenv("DECKHAND_PROJECT", "2")
    settings = config.load(tmp_path)

    with pytest.raises(fields.NotOnBoard) as info:
        fields.set_field(settings, REPO, 248, "Kind", "fix")

    assert "not on project acme/2" in str(info.value)
    graphql = [call for call in gh_calls() if call.startswith("api graphql")]
    assert "projectsV2(first" in graphql[0]
