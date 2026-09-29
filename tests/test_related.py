"""The issues a story names or is linked to, with their state and column, read in one query."""

from __future__ import annotations

import json

import pytest

from deckhand import gh, related
from deckhand.config import Settings

REPO = "acme/widgets"


@pytest.fixture
def settings() -> Settings:
    return Settings(resolved=gh.LinkedProject(owner="acme", owner_type="Organization", number=2, title="Widgets"))


def test_referenced_finds_local_and_qualified_issues_once_each_in_order():
    body = "Builds on #257 and acme/gadgets#13, then #253.\nAgain #257.\n"
    assert related.referenced(body, REPO) == [(REPO, 257), ("acme/gadgets", 13), (REPO, 253)]


def test_referenced_skips_fenced_code():
    body = "Uses #257.\n\n```go\n// see #999\n```\n\nThen #253.\n"
    assert related.referenced(body, REPO) == [(REPO, 257), (REPO, 253)]


def test_referenced_closes_a_fence_only_on_its_own_marker():
    body = "```\n~~~\n#1\n```\nThen #2.\n"
    assert related.referenced(body, REPO) == [(REPO, 2)]


def test_referenced_skips_url_fragments_and_words():
    body = "See https://example.com/page#3 and C# and issue#4 but not a heading.\n## Plan\n"
    assert related.referenced(body, REPO) == []


def test_referenced_skips_html_entities_code_spans_and_oversized_numbers():
    body = "It&#39;s `#12` and #1234567890 but #123456789 and #5.\n"
    assert related.referenced(body, REPO) == [(REPO, 123456789), (REPO, 5)]


def test_referenced_folds_the_story_repository_without_case():
    body = "See ACME/Widgets#7 and acme/gadgets#8.\n"
    assert related.referenced(body, REPO) == [(REPO, 7), ("acme/gadgets", 8)]


def test_read_asks_once_and_renders_each_issue(fake_gh, gh_calls, settings):
    body = "Needs #257 and acme/gadgets#13; #253 landed; see #281. This is #248."

    found = related.read(settings, REPO, 248, body)

    assert len([c for c in gh_calls() if "issueOrPullRequest(" in c]) == 1
    assert related.lines(found, REPO) == [
        "#253 closed Done Seed the role matrix",
        "#257 open Backlog Export the role matrix",
        "#281 merged off the board Cut over to the authority",
        "acme/gadgets#13 open off the board Deploy",
    ]


def test_read_adds_blockers_and_what_the_story_blocks(fake_gh, gh_calls, settings, monkeypatch):
    monkeypatch.setenv("GH_BLOCKED_BY", json.dumps([{"number": 257, "state": "open", "title": "Export"}]))
    monkeypatch.setenv("GH_BLOCKING", json.dumps([{"number": 253, "state": "open", "title": "Seed"}]))

    found = related.read(settings, REPO, 248, "No references here.")

    assert sorted(item.number for item in found) == [253, 257]


def test_read_leaves_out_what_it_is_told_to_skip(fake_gh, gh_calls, settings):
    related.read(settings, REPO, 248, "Needs #257 and #253.", frozenset({(REPO, 257)}))

    (query,) = [c for c in gh_calls() if "issueOrPullRequest(" in c]
    assert "i253:" in query
    assert "i257:" not in query


def test_read_leaves_the_story_itself_out(fake_gh, gh_calls, settings):
    related.read(settings, REPO, 248, "This is #248 and needs #257.")

    (query,) = [c for c in gh_calls() if "issueOrPullRequest(" in c]
    assert "i257:" in query
    assert "i248:" not in query


def test_read_keeps_what_resolved_when_the_query_fails_with_data(fake_gh, settings, monkeypatch):
    monkeypatch.setenv("GH_RELATED_ERROR", "Could not resolve to an issue or pull request with the number of 999")

    found = related.read(settings, REPO, 248, "Needs #257, #253 and #999.")

    assert [item.number for item in found] == [257, 253]


def test_read_raises_when_the_failed_query_has_no_data(fake_gh, settings, monkeypatch, tmp_path):
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    monkeypatch.setenv("GH_RELATED_ERROR", "boom")
    monkeypatch.setenv("GH_RELATED_FILE", str(tmp_path / "bad.json"))

    with pytest.raises(gh.GhError, match="boom"):
        related.read(settings, REPO, 248, "Needs #257.")


def test_read_with_nothing_to_ask_makes_no_query(fake_gh, gh_calls, settings):
    assert related.read(settings, REPO, 248, "Nothing named.") == []
    assert not [c for c in gh_calls() if "issueOrPullRequest(" in c]


def test_lines_are_capped():
    many = [related.Related(REPO, n, "open", "Backlog", f"Story {n}") for n in range(1, 31)]
    shown = related.lines(many, REPO)
    assert len(shown) == related.LIMIT + 1
    assert shown[-1] == "and 5 more"
