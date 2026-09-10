from __future__ import annotations

import json
from pathlib import Path

from deckhand import board
from tests.conftest import FIXTURES

REPO = "acme/widgets"


def _node(number: int, title: str, status: str | None, repo: str = REPO, closed: str | None = None) -> dict:
    values = [] if status is None else [{"name": status, "field": {"name": "Status"}}]
    return {
        "content": {"number": number, "closedAt": closed, "title": title, "repository": {"nameWithOwner": repo}},
        "fieldValues": {"nodes": values},
    }


def _board(tmp_path: Path, monkeypatch, nodes: list[dict]) -> None:
    """Point the fake's items query at a one-page board holding `nodes`."""
    data = {"data": {"organization": {"projectV2": {"items": {"pageInfo": {"hasNextPage": False}, "nodes": nodes}}}}}
    path = tmp_path / "items.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(path))


def test_add_puts_the_issue_on_the_project(fake_gh, gh_calls, settings):
    board.add(settings, "https://github.com/acme/widgets/issues/248")

    assert gh_calls() == [
        "project item-add 2 --owner acme --url https://github.com/acme/widgets/issues/248 --format json"
    ]


def test_items_query_names_the_pagination_variable_end_cursor_so_gh_paginate_advances():
    assert "after:$endCursor" in board.ITEMS_QUERY
    assert "$after" not in board.ITEMS_QUERY


def test_items_reads_every_node_across_pages(fake_gh, settings, tmp_path, monkeypatch):
    items = json.loads((FIXTURES / "graphql-project-items.json").read_text(encoding="utf-8"))
    nodes = items["data"]["organization"]["projectV2"]["items"]["nodes"]
    pages = []
    for i, chunk in enumerate((nodes[:2], nodes[2:])):
        page = json.loads(json.dumps(items))
        page["data"]["organization"]["projectV2"]["items"]["nodes"] = chunk
        page["data"]["organization"]["projectV2"]["items"]["pageInfo"] = {"hasNextPage": i == 0, "endCursor": "c1"}
        path = tmp_path / f"page{i}.json"
        path.write_text(json.dumps(page), encoding="utf-8")
        pages.append(str(path))
    monkeypatch.setenv("GH_PAGES", ":".join(pages))

    found = board.items(settings)

    assert [node["content"]["number"] for node in found] == [210, 211, 96, 300]


def test_oldest_open_is_the_lowest_numbered_open_story_of_the_repository_that_is_not_done(
    fake_gh, settings, tmp_path, monkeypatch
):
    _board(
        tmp_path,
        monkeypatch,
        [
            _node(9, "Nine", "Done"),
            _node(7, "Seven", "Draft"),
            _node(5, "Five", None, closed="2026-09-01T00:00:00Z"),
            _node(4, "Four", "Backlog"),
            _node(2, "Elsewhere", "Backlog", repo="acme/other"),
            {"content": {}, "fieldValues": {"nodes": []}},
        ],
    )

    assert board.oldest_open(settings, REPO, exclude=4) == (7, "Seven")
    assert board.oldest_open(settings, REPO, exclude=0) == (4, "Four")


def test_oldest_open_counts_a_story_with_no_status_as_open(fake_gh, settings, tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, [_node(7, "Seven", "Draft"), _node(3, "Three", None), _node(4, "Four", "Backlog")])

    assert board.oldest_open(settings, REPO, exclude=0) == (3, "Three")


def test_oldest_open_is_none_on_an_empty_board(fake_gh, settings, tmp_path, monkeypatch):
    _board(tmp_path, monkeypatch, [])

    assert board.oldest_open(settings, REPO, exclude=0) is None


def test_view_fields_names_the_fields_each_view_shows(fake_gh, settings):
    assert board.view_fields(settings) == {
        "Board": ["Title", "Status", "Kind", "Story Points", "Actual", "Assignees", "Repository"],
        "Table": ["Title", "Assignees", "Status", "Labels"],
    }
