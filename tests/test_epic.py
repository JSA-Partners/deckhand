"""The epic command: open one, add stories to it, list them, and forecast one in dates."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from deckhand import cli, epic

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "acme/widgets"


@pytest.fixture
def board_env(fake_gh, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "epic-items.json"))
    monkeypatch.setattr(epic, "_today", lambda: date(2026, 10, 2))
    return tmp_path


def _issue_file(tmp_path, monkeypatch, number: int, label: str) -> None:
    path = tmp_path / f"issue-{number}.json"
    path.write_text(
        json.dumps(
            {
                "number": number,
                "title": "T",
                "body": "",
                "url": f"https://github.com/{REPO}/issues/{number}",
                "state": "OPEN",
                "comments": [],
                "labels": [{"name": label}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(f"GH_ISSUE_FILE_{number}", str(path))


# open


def test_open_creates_a_labelled_issue_and_boards_it(board_env, gh_calls, capsys):
    assert cli.main(["epic", "open", "Permission rework", "--about", "Guests and registry ownership."]) == 0

    calls = gh_calls()
    created = next(call for call in calls if call.startswith("issue create"))
    assert "--title Permission rework" in created
    assert created.endswith("--label epic")
    assert any(call.startswith("project item-add") for call in calls)
    assert "Opened epic #999" in capsys.readouterr().out


def test_open_refuses_without_what_the_epic_delivers(board_env, gh_calls, capsys):
    assert cli.main(["epic", "open", "Permission rework"]) == 1

    assert "--about" in capsys.readouterr().err
    assert not any(call.startswith("issue create") for call in gh_calls())


# add


def test_add_makes_each_story_a_sub_issue_of_the_epic(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "epic")

    assert cli.main(["epic", "add", "300", "302", "acme/gadgets#9"]) == 0

    assert [call for call in gh_calls() if "sub_issues" in call] == [
        "api -X POST repos/acme/widgets/issues/300/sub_issues -F sub_issue_id=5099965156 -F replace_parent=true",
        "api -X POST repos/acme/widgets/issues/300/sub_issues -F sub_issue_id=5099965156 -F replace_parent=true",
    ]
    out = capsys.readouterr().out
    assert "#302 joined #300" in out
    assert "acme/gadgets#9 joined #300" in out


def test_add_refuses_an_issue_that_is_not_an_epic(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "deckhand")

    assert cli.main(["epic", "add", "300", "302"]) == 1

    assert "#300 is not an epic" in capsys.readouterr().err
    assert not any("POST" in call for call in gh_calls())


def test_add_refuses_without_a_story(board_env, gh_calls, capsys):
    assert cli.main(["epic", "add", "300"]) == 1

    assert "epic add <epic> <story>..." in capsys.readouterr().err
    assert not any("POST" in call for call in gh_calls())


# list


def test_list_counts_the_pieces_of_every_epic(board_env, capsys):
    assert cli.main(["epic", "list", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == [
        {
            "epic": "acme/widgets#300",
            "title": "Permission rework",
            "about": "Guests and registry ownership.",
            "closed": False,
            "pieces": 2,
            "done": 1,
            "drafts": 0,
        }
    ]


def test_list_reads_as_a_line_per_epic(board_env, capsys):
    assert cli.main(["epic", "list"]) == 0

    assert capsys.readouterr().out.splitlines() == ["#300 Permission rework: 1 of 2 pieces done, 0 drafts"]


# forecast


def test_forecast_gives_the_floor_and_the_commitment_as_dates(board_env, capsys):
    assert cli.main(["epic", "forecast", "300", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "epic": "acme/widgets#300",
        "title": "Permission rework",
        "about": "Guests and registry ownership.",
        "closed": False,
        "pieces": 2,
        "done": 1,
        "drafts": 0,
        "at_once": 1,
        "floor": "2026-10-03",
        "commitment": "2026-10-03",
        "worst": "2026-10-03",
        "samples": 1,
        "thin": True,
        "growth": 1.0,
        "idle": 0.0,
    }


def test_forecast_prints_the_block_for_one_epic(board_env, capsys):
    assert cli.main(["epic", "forecast", "300"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0] == "## Forecast: Permission rework"
    assert "  1 story, 1 points, 1 at once, open sessions" in out
    assert "Commitment" in out


def test_forecast_refuses_an_issue_that_is_not_an_epic_on_the_board(board_env, capsys):
    assert cli.main(["epic", "forecast", "304"]) == 1

    assert "#304 is not an epic on the board" in capsys.readouterr().err


def test_a_story_closed_as_not_planned_is_not_a_piece(board_env, capsys):
    assert cli.main(["epic", "list", "--json"]) == 0

    [row] = json.loads(capsys.readouterr().out)
    assert (row["pieces"], row["done"], row["drafts"]) == (2, 1, 0)
