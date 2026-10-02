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

    calls = gh_calls()
    assert [call for call in calls if "sub_issues" in call] == [
        "api -X POST repos/acme/widgets/issues/300/sub_issues -F sub_issue_id=5099965156 -F replace_parent=true",
        "api -X POST repos/acme/widgets/issues/300/sub_issues -F sub_issue_id=5099965156 -F replace_parent=true",
    ]
    # The fake answers one id for every issue, so only the paths show each story was the one looked up.
    assert "api repos/acme/widgets/issues/302" in calls
    assert "api repos/acme/gadgets/issues/9" in calls
    out = capsys.readouterr().out
    assert "#302 joined #300" in out
    assert "acme/gadgets#9 joined #300" in out


def test_add_names_a_story_given_twice_once(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "epic")

    assert cli.main(["epic", "add", "300", "302", "302", "acme/widgets#302"]) == 0

    assert len([call for call in gh_calls() if "POST" in call]) == 1
    assert capsys.readouterr().out.splitlines() == ["#302 joined #300"]


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
        "floor": "2026-10-30",
        "commitment": "2026-12-04",
        "worst": "2027-01-08",
        "pace": "project",
        "weeks": 5,
        "per_week": 0.2,
        "unsplit": 0,
        "split_size": 1.0,
        "split_basis": None,
        "reason": None,
    }


def test_forecast_prints_the_block_for_one_epic(board_env, capsys):
    assert cli.main(["epic", "forecast", "300"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "## Forecast: Permission rework",
        "  1 piece left",
        "",
        "  Floor         4 weeks   50th percentile",
        "  Commitment    9 weeks   85th percentile",
        "  Worst seen   14 weeks   95th percentile",
        "",
        "  Paced by the whole project's last 5 weeks, an average of 0.2 stories a week.",
        "  That includes work outside this epic, so it leans early.",
    ]


def test_forecast_refuses_an_issue_that_is_not_an_epic_on_the_board(board_env, capsys):
    assert cli.main(["epic", "forecast", "304"]) == 1

    assert "#304 is not an epic on the board" in capsys.readouterr().err


def test_a_dropped_story_is_neither_a_piece_nor_left_to_forecast(board_env, capsys):
    assert cli.main(["epic", "forecast", "300", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["pieces"] == 2

    assert cli.main(["epic", "forecast", "300"]) == 0
    assert "  1 piece left" in capsys.readouterr().out.splitlines()


def _member(number: int, repo: str = REPO) -> dict:
    return {
        "id": f"I_{number}",
        "isArchived": False,
        "content": {
            "number": number,
            "title": "More",
            "url": f"https://github.com/{repo}/issues/{number}",
            "state": "OPEN",
            "closedAt": None,
            "body": "",
            "repository": {"nameWithOwner": repo},
            "labels": {"nodes": [{"name": "deckhand"}]},
            "parent": {"number": 300, "repository": {"nameWithOwner": REPO}},
            "comments": {"nodes": []},
        },
        "fieldValues": {"nodes": [{"name": "Backlog", "field": {"name": "Status"}}]},
    }


def _with_members(tmp_path, monkeypatch, *nodes: dict) -> None:
    items = json.loads((FIXTURES / "epic-items.json").read_text(encoding="utf-8"))
    items["data"]["organization"]["projectV2"]["items"]["nodes"].extend(nodes)
    path = tmp_path / "more-items.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(path))


def test_forecast_counts_a_member_in_another_repository(board_env, monkeypatch, capsys):
    _with_members(board_env, monkeypatch, _member(7, repo="acme/gadgets"))

    assert cli.main(["epic", "forecast", "300", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert (found["pieces"], found["done"], found["pace"]) == (3, 1, "project")
    assert found["commitment"] > "2026-12-04"

    assert cli.main(["epic", "forecast", "300"]) == 0
    assert "  2 pieces left" in capsys.readouterr().out.splitlines()


def test_forecast_gives_no_dates_when_the_pace_would_take_over_two_years(board_env, monkeypatch, capsys):
    _with_members(board_env, monkeypatch, *[_member(400 + index) for index in range(30)])

    assert cli.main(["epic", "forecast", "300", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert (found["floor"], found["commitment"], found["worst"], found["pace"]) == (None, None, None, None)
    assert (found["pieces"], found["per_week"], found["reason"]) == (32, 0.2, "over two years")

    assert cli.main(["epic", "forecast", "300"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "## Forecast: Permission rework",
        "  31 pieces left",
        "",
        "  The measured pace would take over two years, so there is no date range.",
    ]


def test_forecast_finds_an_epic_named_in_another_case(board_env, capsys):
    assert cli.main(["epic", "forecast", "Acme/Widgets#300", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["epic"] == "acme/widgets#300"


def _in_epic(monkeypatch, *numbers: int) -> None:
    parent = {"number": 300, "repository_url": f"https://api.github.com/repos/{REPO}"}
    monkeypatch.setenv("GH_PARENT", json.dumps({str(number): parent for number in numbers}))


def test_add_refuses_a_bad_story_ref_before_any_write(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "epic")

    assert cli.main(["epic", "add", "300", "302", "not-a-ref"]) == 1

    assert "not-a-ref" in capsys.readouterr().err
    assert not any("POST" in call for call in gh_calls())


def test_add_refuses_an_issue_that_is_not_a_deckhand_story_before_any_write(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "epic")
    _issue_file(board_env, monkeypatch, 302, "bug")

    assert cli.main(["epic", "add", "300", "301", "302"]) == 1

    assert "#302 is not a deckhand story" in capsys.readouterr().err
    assert not any("POST" in call for call in gh_calls())


def test_add_refuses_an_epic_as_a_story_before_any_write(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "epic")
    _issue_file(board_env, monkeypatch, 302, "epic")

    assert cli.main(["epic", "add", "300", "301", "302"]) == 1

    assert "#302 is an epic" in capsys.readouterr().err
    assert not any("POST" in call for call in gh_calls())


def test_add_skips_a_story_already_in_the_epic(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "epic")
    _in_epic(monkeypatch, 302)

    assert cli.main(["epic", "add", "300", "302"]) == 0

    assert "#302 already in #300" in capsys.readouterr().out
    assert not any("POST" in call for call in gh_calls())


def test_add_names_the_epic_a_story_moved_from(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "epic")
    parent = {"number": 400, "repository_url": f"https://api.github.com/repos/{REPO}"}
    elsewhere = {"number": 12, "repository_url": "https://api.github.com/repos/acme/gadgets"}
    monkeypatch.setenv("GH_PARENT", json.dumps({"302": parent, "9": elsewhere}))

    assert cli.main(["epic", "add", "300", "302", "acme/gadgets#9"]) == 0

    assert len([call for call in gh_calls() if "POST" in call]) == 2
    assert capsys.readouterr().out.splitlines() == [
        "#302 moved from #400 to #300",
        "acme/gadgets#9 moved from acme/gadgets#12 to #300",
    ]


def test_add_refuses_a_story_the_api_cannot_find_before_any_write(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "epic")
    monkeypatch.setenv("GH_ISSUE_ID_FAILS", "9")

    assert cli.main(["epic", "add", "300", "302", "acme/gadgets#9"]) == 1

    assert "Not Found" in capsys.readouterr().err
    assert not any("POST" in call for call in gh_calls())


def test_add_finds_an_epic_named_in_another_case(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "epic")
    _in_epic(monkeypatch, 302)

    assert cli.main(["epic", "add", "Acme/Widgets#300", "302"]) == 0

    assert capsys.readouterr().out.splitlines() == ["#302 already in #300"]
    assert not any("POST" in call for call in gh_calls())


def test_add_writes_only_the_stories_not_yet_in_the_epic(board_env, gh_calls, monkeypatch, capsys):
    _issue_file(board_env, monkeypatch, 300, "epic")
    _in_epic(monkeypatch, 302)

    assert cli.main(["epic", "add", "300", "301", "302"]) == 0

    assert [call for call in gh_calls() if "POST" in call] == [
        "api -X POST repos/acme/widgets/issues/300/sub_issues -F sub_issue_id=5099965156 -F replace_parent=true",
    ]
    out = capsys.readouterr().out
    assert "#301 joined #300" in out
    assert "#302 already in #300" in out


def test_two_forecasts_of_an_unchanged_board_give_the_same_dates(board_env, capsys, monkeypatch):
    seeds = []
    real = epic.throughput.outlook

    def _outlook(*args, **kwargs):
        seeds.append(kwargs.get("seed"))
        return real(*args, **kwargs)

    monkeypatch.setattr(epic.throughput, "outlook", _outlook)

    assert cli.main(["epic", "forecast", "300", "--json"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["epic", "forecast", "300", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == json.loads(first)
    assert seeds == [epic.SEED, epic.SEED]


def _archived_and_closed(tmp_path, monkeypatch) -> None:
    items = json.loads((FIXTURES / "epic-items.json").read_text(encoding="utf-8"))
    for node in items["data"]["organization"]["projectV2"]["items"]["nodes"]:
        if node["content"]["number"] in (300, 302):
            node["content"].update(state="CLOSED", closedAt="2026-09-20T00:00:00Z")
        if node["content"]["number"] == 300:
            node["isArchived"] = True
    path = tmp_path / "archived-epic.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(path))


def test_forecast_reads_a_finished_epic_the_board_archived(board_env, monkeypatch, capsys):
    _archived_and_closed(board_env, monkeypatch)

    assert cli.main(["epic", "forecast", "300", "--json"]) == 0

    found = json.loads(capsys.readouterr().out)
    assert (found["closed"], found["pieces"], found["done"]) == (True, 2, 2)
    assert (found["floor"], found["commitment"], found["worst"], found["pace"]) == (None, None, None, None)


def test_list_leaves_out_an_archived_epic(board_env, monkeypatch, capsys):
    _archived_and_closed(board_env, monkeypatch)

    assert cli.main(["epic", "list", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == []
