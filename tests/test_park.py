"""A parked feature: where it opens, what it says it came from, and what it waits on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import draft
from tests.conftest import labeled_board, run_deckhand


@pytest.fixture(autouse=True)
def _every_story_has_a_feature(feature_field):
    """Every story is written into a Feature, so every test here runs on a project that has the field."""


FEATURE = "## Requirements\n\nGuests should be able to share a collection with a guest.\n"
TITLE = "Share a collection"


def _park(repo: Path, tmp_path: Path, *flags: str, copy: Path | None = None):
    """Park FEATURE with `flags`; `copy` collects the bodies, which reach gh through a file."""
    source = tmp_path / "feature.md"
    source.write_text(FEATURE, encoding="utf-8")
    env = {"GH_BODY_FILE_COPY": str(copy)} if copy is not None else {}
    return run_deckhand("new", "apply", "--park", "--feature", "Billing", *flags, str(source), cwd=repo, env=env)


def test_a_park_names_the_open_stories_that_look_like_it_in_any_repository(fake_gh, repo, tmp_path, monkeypatch):
    board = labeled_board(
        tmp_path,
        ("acme/gadgets", 321, "Compare times as times in the publications list test"),
        ("acme/widgets", 12, "Share a collection"),
    )
    for name, value in board.items():
        monkeypatch.setenv(name, value)

    result = _park(repo, tmp_path, "--title", "Compare times, not strings, in list sort tests")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == (
        "Similar: acme/gadgets#321 Draft Compare times as times in the publications list test"
    )
    assert "#12" not in result.stdout


def test_a_parked_feature_is_boarded_as_draft(fake_gh, gh_calls, repo, tmp_path):
    """A parked feature has no settled requirements yet, which is Draft."""
    result = _park(repo, tmp_path, "--title", TITLE)

    assert result.returncode == 0, result.stderr
    assert "--field-id PVTSSF_STATUS --single-select-option-id opt_draft" in "\n".join(gh_calls())


def test_from_names_the_origin_and_writes_no_dependency(fake_gh, gh_calls, repo, tmp_path):
    copy = tmp_path / "bodies.md"

    result = _park(repo, tmp_path, "--title", TITLE, "--from", "251", copy=copy)

    assert result.returncode == 0, result.stderr
    assert "Drafted: parked from #251" in copy.read_text(encoding="utf-8")
    assert [c for c in gh_calls() if "dependencies/blocked_by" in c] == []


def test_a_park_with_no_origin_names_its_repository(fake_gh, repo, tmp_path):
    copy = tmp_path / "bodies.md"

    result = _park(repo, tmp_path, "--title", TITLE, copy=copy)

    assert result.returncode == 0, result.stderr
    assert "Drafted: parked from acme/widgets" in copy.read_text(encoding="utf-8")


def test_after_records_what_the_park_waits_on(fake_gh, repo, tmp_path):
    result = _park(repo, tmp_path, "--title", TITLE, "--after", "251")

    assert result.returncode == 0, result.stderr
    assert "blocked by #251" in result.stdout


def test_blocks_still_names_the_origin_and_writes_the_dependency(fake_gh, gh_calls, repo, tmp_path):
    copy = tmp_path / "bodies.md"

    result = _park(repo, tmp_path, "--title", TITLE, "--blocks", "251", copy=copy)

    assert result.returncode == 0, result.stderr
    assert "Drafted: parked from #251" in copy.read_text(encoding="utf-8")
    assert [c for c in gh_calls() if "dependencies/blocked_by" in c] != []


def test_from_and_blocks_together_are_a_usage_error(fake_gh, repo, tmp_path):
    result = _park(repo, tmp_path, "--title", TITLE, "--from", "251", "--blocks", "252")

    assert result.returncode == 2
    assert "--from and --blocks" in result.stderr


def test_after_outside_a_park_is_a_usage_error(fake_gh, repo, tmp_path):
    source = tmp_path / "feature.md"
    source.write_text(FEATURE, encoding="utf-8")

    result = run_deckhand("new", "apply", "--split", "--after", "251", str(source), cwd=repo, env={})

    assert result.returncode == 2
    assert "--after" in result.stderr


def test_a_bad_after_reference_is_refused_before_any_write(fake_gh, gh_calls, repo, tmp_path):
    result = _park(repo, tmp_path, "--title", TITLE, "--after", "not-a-number")

    assert result.returncode == 1
    assert "owner/name#M" in result.stderr
    assert [c for c in gh_calls() if c.startswith("issue create")] == []


def test_blocks_and_after_that_close_a_loop_refuse_before_any_write(fake_gh, gh_calls, repo, tmp_path, monkeypatch):
    mapped = tmp_path / "blocked-by.json"
    back = {"number": 251, "title": "This story", "state": "open", "repository": {"full_name": "acme/widgets"}}
    mapped.write_text(json.dumps({"13": [back]}), encoding="utf-8")
    monkeypatch.setenv("GH_BLOCKED_BY_MAP", str(mapped))

    result = _park(repo, tmp_path, "--title", TITLE, "--blocks", "251", "--after", "acme/gadgets#13")

    assert result.returncode == 1
    assert "#251 -> acme/gadgets#13 -> #251" in result.stderr
    assert not [c for c in gh_calls() if c.startswith("issue create") or "-X POST" in c]


def test_split_after_can_only_name_an_earlier_story():
    """A split wires each story to stories above it only, so it cannot close a loop among its own."""
    text = json.dumps(
        {
            "kind": "split",
            "requirements": "Guests share collections.",
            "stories": [
                {"key": "a", "title": "One", "sentence": "First.", "after": ["b"]},
                {"key": "b", "title": "Two", "sentence": "Second."},
            ],
        }
    )
    with pytest.raises(ValueError, match="not a story defined before it"):
        draft.parse_split(text)


def test_a_park_whose_field_read_fails_creates_nothing(fake_gh, gh_calls, repo, tmp_path, monkeypatch):
    """Every field a park writes is read first, so a slow read leaves no stray issue behind."""
    monkeypatch.setenv("GH_FIELD_LIST_FAILS", "1")

    result = _park(repo, tmp_path, "--title", TITLE)

    assert result.returncode == 1
    assert not [call for call in gh_calls() if call.startswith(("issue create", "project item-add"))]


def test_a_feature_write_that_fails_names_the_epic_add_that_finishes_it(fake_gh, gh_calls, repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_ITEM_EDIT_FAILS_FOR", "OPT_BILLING")

    result = _park(repo, tmp_path, "--title", TITLE)

    assert result.returncode == 1
    assert "Feature not set; epic add OPT_BILLING acme/widgets#" in result.stderr
    assert "--field-id PVTSSF_STATUS --single-select-option-id opt_draft" in "\n".join(gh_calls())
    assert [call for call in gh_calls() if call.startswith("issue comment")]
