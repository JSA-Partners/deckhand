"""The epic command: open a feature, add stories to it, list every feature, and forecast one in dates."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from deckhand import cli, epic

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "acme/widgets"
EXISTING = [
    {"id": "OPT_BILLING", "name": "Billing", "color": "GREEN", "description": "Charge for seats."},
    {
        "id": "OPT_PERMISSION",
        "name": "Permission rework",
        "color": "BLUE",
        "description": "Guests and registry ownership.",
    },
    {"id": "OPT_AUDIT", "name": "Permission audit", "color": "PURPLE", "description": "Who saw what, logged."},
]


@pytest.fixture
def board_env(fake_gh, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "epic-items.json"))
    monkeypatch.setenv("GH_PROJECT_FIELDS_FILE", str(FIXTURES / "feature-fields.json"))
    monkeypatch.setattr(epic, "_today", lambda: date(2026, 10, 2))
    return tmp_path


def _payload(call: str) -> dict:
    return json.loads(call.split("--input - ", 1)[1])


def _writes(calls: list[str]) -> list[str]:
    return [
        call
        for call in calls
        if "ProjectV2Field(" in call or call.startswith(("project item-edit", "issue create", "project item-add"))
    ]


# open


def test_open_creates_the_feature_field_with_the_first_option(board_env, gh_calls, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_FIELDS_FILE", str(FIXTURES / "project-fields.json"))

    assert cli.main(["epic", "open", "Permission rework", "--about", "Guests and registry ownership."]) == 0

    (created,) = [call for call in gh_calls() if "createProjectV2Field" in call]
    payload = _payload(created)
    assert payload["variables"]["name"] == "Feature"
    assert payload["variables"]["options"] == [
        {"name": "Permission rework", "color": epic.COLOR, "description": "Guests and registry ownership."}
    ]
    assert _writes(gh_calls()) == [created]
    assert capsys.readouterr().out.splitlines() == ["Created the Feature field with Permission rework"]


def test_open_appends_the_option_and_resends_every_existing_one_by_id(board_env, gh_calls, capsys):
    assert cli.main(["epic", "open", "Seat  limits", "--about", "  Cap the seats a plan holds. "]) == 0

    (updated,) = [call for call in gh_calls() if "updateProjectV2Field(" in call]
    payload = _payload(updated)
    assert payload["variables"] == {
        "field": "PVTSSF_FEATURE",
        "options": [
            *EXISTING,
            {"name": "Seat limits", "color": epic.COLOR, "description": "Cap the seats a plan holds."},
        ],
    }
    assert _writes(gh_calls()) == [updated]
    assert capsys.readouterr().out.splitlines() == ["Opened feature Seat limits, last of 4"]


def test_open_refuses_a_name_already_taken_in_any_case(board_env, gh_calls, capsys):
    assert cli.main(["epic", "open", "permission REWORK", "--about", "Again."]) == 1

    assert "Permission rework is already a feature" in capsys.readouterr().err
    assert _writes(gh_calls()) == []


def test_open_refuses_without_what_the_feature_delivers(board_env, gh_calls, capsys):
    assert cli.main(["epic", "open", "Permission rework"]) == 1

    assert "--about" in capsys.readouterr().err
    assert _writes(gh_calls()) == []


def test_open_refuses_without_a_name(board_env, gh_calls, capsys):
    assert cli.main(["epic", "open", "--about", "Something."]) == 1

    assert 'epic open "<name>"' in capsys.readouterr().err
    assert _writes(gh_calls()) == []


def test_open_refuses_a_feature_field_that_is_not_a_single_select(board_env, gh_calls, tmp_path, monkeypatch, capsys):
    path = tmp_path / "text-feature.json"
    nodes = [{"id": "PVTF_FEATURE", "name": "Feature", "dataType": "TEXT"}]
    path.write_text(json.dumps({"data": {"organization": {"projectV2": {"fields": {"nodes": nodes}}}}}))
    monkeypatch.setenv("GH_PROJECT_FIELDS_FILE", str(path))

    assert cli.main(["epic", "open", "Seat limits", "--about", "Cap seats."]) == 1

    assert "Feature is TEXT, not a single select" in capsys.readouterr().err
    assert _writes(gh_calls()) == []


# add


def _edit(item: str, option: str) -> str:
    return (
        f"project item-edit --id {item} --project-id PVT_TEST --field-id PVTSSF_FEATURE "
        f"--single-select-option-id {option}"
    )


def test_add_sets_the_feature_on_each_story_s_item(board_env, gh_calls, capsys):
    assert cli.main(["epic", "add", "Permission audit", "302", "Acme/Widgets#304"]) == 0

    assert _writes(gh_calls()) == [_edit("I_302", "OPT_AUDIT"), _edit("I_304", "OPT_AUDIT")]
    assert capsys.readouterr().out.splitlines() == [
        "#302 moved from Permission rework to Permission audit",
        "#304 joined Permission audit",
    ]


def test_add_takes_a_unique_prefix_in_any_case_or_an_option_id(board_env, gh_calls, capsys):
    assert cli.main(["epic", "add", "bill", "304"]) == 0
    assert cli.main(["epic", "add", "OPT_BILLING", "304"]) == 0

    assert _writes(gh_calls()) == [_edit("I_304", "OPT_BILLING"), _edit("I_304", "OPT_BILLING")]


def test_add_prefers_an_exact_name_to_a_longer_one_it_starts(board_env, gh_calls, tmp_path, monkeypatch):
    data = json.loads((FIXTURES / "feature-fields.json").read_text(encoding="utf-8"))
    feature = data["data"]["organization"]["projectV2"]["fields"]["nodes"][-1]
    feature["options"].append({"id": "OPT_BILL", "name": "Bill", "color": "RED", "description": ""})
    path = tmp_path / "bill.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("GH_PROJECT_FIELDS_FILE", str(path))

    assert cli.main(["epic", "add", "bill", "304"]) == 0

    assert _writes(gh_calls()) == [_edit("I_304", "OPT_BILL")]


def test_add_names_a_story_given_twice_once(board_env, gh_calls, capsys):
    assert cli.main(["epic", "add", "Billing", "304", "304", "acme/widgets#304"]) == 0

    assert _writes(gh_calls()) == [_edit("I_304", "OPT_BILLING")]
    assert capsys.readouterr().out.splitlines() == ["#304 joined Billing"]


def test_add_skips_a_story_already_in_the_feature(board_env, gh_calls, capsys):
    assert cli.main(["epic", "add", "Permission rework", "302", "304"]) == 0

    assert _writes(gh_calls()) == [_edit("I_304", "OPT_PERMISSION")]
    assert capsys.readouterr().out.splitlines() == [
        "#302 already in Permission rework",
        "#304 joined Permission rework",
    ]


def test_add_reaches_a_story_in_another_repository(board_env, gh_calls, monkeypatch, capsys):
    _with_members(board_env, monkeypatch, _member(9, repo="acme/gadgets", feature=None))

    assert cli.main(["epic", "add", "Billing", "acme/gadgets#9"]) == 0

    assert _writes(gh_calls()) == [_edit("I_9", "OPT_BILLING")]
    assert capsys.readouterr().out.splitlines() == ["acme/gadgets#9 joined Billing"]


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (
            ["Shipping", "304"],
            "no feature matches Shipping; the features are Billing, Permission rework, Permission audit",
        ),
        (["perm", "304"], "perm matches Permission rework and Permission audit; name one in full"),
        (["Billing", "304", "999"], "#999 is not on the board"),
        (["Billing", "304", "303"], "#303 is not a deckhand story"),
        (["Billing", "304", "305"], "#305 was closed as not planned or a duplicate"),
        (["Billing", "304", "not-a-ref"], "not-a-ref"),
        (["Billing"], "epic add <feature> <story>..."),
    ],
)
def test_add_refuses_before_any_write(board_env, gh_calls, capsys, args, said):
    assert cli.main(["epic", "add", *args]) == 1

    assert said in capsys.readouterr().err
    assert _writes(gh_calls()) == []


def test_add_refuses_when_no_feature_was_ever_opened(board_env, gh_calls, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_FIELDS_FILE", str(FIXTURES / "project-fields.json"))

    assert cli.main(["epic", "add", "Billing", "304"]) == 1

    assert "no Feature field yet; open a feature with epic open" in capsys.readouterr().err
    assert _writes(gh_calls()) == []


def test_add_reaches_an_archived_story(board_env, gh_calls, monkeypatch):
    _archived_and_closed(board_env, monkeypatch)

    assert cli.main(["epic", "add", "Billing", "301"]) == 0

    assert _writes(gh_calls()) == [_edit("I_301", "OPT_BILLING")]


# list


def test_list_counts_the_pieces_of_every_feature_in_field_order(board_env, capsys):
    assert cli.main(["epic", "list", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == [
        {
            "epic": "OPT_BILLING",
            "title": "Billing",
            "about": "Charge for seats.",
            "closed": False,
            "pieces": 1,
            "done": 0,
            "drafts": 0,
        },
        {
            "epic": "OPT_PERMISSION",
            "title": "Permission rework",
            "about": "Guests and registry ownership.",
            "closed": False,
            "pieces": 2,
            "done": 1,
            "drafts": 0,
        },
        {
            "epic": "OPT_AUDIT",
            "title": "Permission audit",
            "about": "Who saw what, logged.",
            "closed": False,
            "pieces": 0,
            "done": 0,
            "drafts": 0,
        },
    ]


def test_list_reads_as_a_line_per_feature(board_env, capsys):
    assert cli.main(["epic", "list"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Billing: 0 of 1 pieces done, 0 drafts. Charge for seats.",
        "Permission rework: 1 of 2 pieces done, 0 drafts. Guests and registry ownership.",
        "Permission audit: 0 of 0 pieces done, 0 drafts. Who saw what, logged.",
    ]


def test_list_follows_the_option_order_not_the_board_order(board_env, tmp_path, monkeypatch, capsys):
    data = json.loads((FIXTURES / "feature-fields.json").read_text(encoding="utf-8"))
    feature = data["data"]["organization"]["projectV2"]["fields"]["nodes"][-1]
    feature["options"].reverse()
    path = tmp_path / "reversed.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("GH_PROJECT_FIELDS_FILE", str(path))

    assert cli.main(["epic", "list", "--json"]) == 0

    assert [row["title"] for row in json.loads(capsys.readouterr().out)] == [
        "Permission audit",
        "Permission rework",
        "Billing",
    ]


def test_list_says_when_no_feature_was_ever_opened(board_env, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_FIELDS_FILE", str(FIXTURES / "project-fields.json"))

    assert cli.main(["epic", "list"]) == 0
    assert capsys.readouterr().out.splitlines() == ["no features yet; open one with epic open"]

    assert cli.main(["epic", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_list_reads_a_feature_whose_every_piece_is_done_as_closed(board_env, monkeypatch, capsys):
    _archived_and_closed(board_env, monkeypatch)

    assert cli.main(["epic", "list", "--json"]) == 0

    rows = {row["title"]: row for row in json.loads(capsys.readouterr().out)}
    assert (rows["Permission rework"]["closed"], rows["Permission rework"]["done"]) == (True, 2)
    assert rows["Permission audit"]["closed"] is False


# forecast


def test_forecast_gives_no_dates_before_five_of_the_feature_s_stories_finish(board_env, monkeypatch, capsys):
    _with_members(board_env, monkeypatch, _member(310, closed_at="2026-09-24T12:00:00Z"))

    assert cli.main(["epic", "forecast", "Permission rework", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "epic": "OPT_PERMISSION",
        "title": "Permission rework",
        "about": "Guests and registry ownership.",
        "closed": False,
        "pieces": 3,
        "done": 2,
        "drafts": 0,
        "floor": None,
        "commitment": None,
        "worst": None,
        "pace": None,
        "weeks": 4,
        "per_week": 0.25,
        "unsplit": 0,
        "split_size": 1.0,
        "split_basis": None,
        "reason": "too few finished",
    }

    assert cli.main(["epic", "forecast", "Permission rework"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "## Forecast: Permission rework",
        "  1 piece left",
        "",
        "  Fewer than 5 of this feature's stories have finished in the weeks measured, so there is no date range yet.",
    ]


def test_forecast_gives_the_floor_and_the_commitment_as_dates(board_env, monkeypatch, capsys):
    _with_members(board_env, monkeypatch, *PACED)

    assert cli.main(["epic", "forecast", "Permission rework", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "epic": "OPT_PERMISSION",
        "title": "Permission rework",
        "about": "Guests and registry ownership.",
        "closed": False,
        "pieces": 7,
        "done": 6,
        "drafts": 0,
        "floor": "2026-10-09",
        "commitment": "2026-10-23",
        "worst": "2026-11-20",
        "pace": "epic",
        "weeks": 4,
        "per_week": 1.25,
        "unsplit": 0,
        "split_size": 1.0,
        "split_basis": None,
        "reason": None,
    }


def test_forecast_prints_the_block_for_one_feature(board_env, monkeypatch, capsys):
    _with_members(board_env, monkeypatch, *PACED)

    assert cli.main(["epic", "forecast", "Permission rework"]) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "## Forecast: Permission rework",
        "  1 piece left",
        "",
        "  Floor         1 week    50th percentile",
        "  Commitment    3 weeks   90th percentile",
        "  Worst case    7 weeks   99th percentile",
        "",
        "  Paced by this feature's own 4 weeks, an average of 1.2 stories a week.",
    ]


@pytest.mark.parametrize("named", ["Permission rework", "permission r", "OPT_PERMISSION"])
def test_forecast_finds_a_feature_by_name_prefix_or_id(board_env, capsys, named):
    assert cli.main(["epic", "forecast", named, "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["epic"] == "OPT_PERMISSION"


@pytest.mark.parametrize(
    ("named", "said"),
    [
        ("Shipping", "no feature matches Shipping"),
        ("Permission", "Permission matches Permission rework and Permission audit"),
    ],
)
def test_forecast_refuses_a_feature_it_cannot_name_one_of(board_env, capsys, named, said):
    assert cli.main(["epic", "forecast", named]) == 1

    assert said in capsys.readouterr().err


def test_forecast_refuses_without_one_feature(board_env, capsys):
    assert cli.main(["epic", "forecast"]) == 1

    assert "epic forecast <feature>" in capsys.readouterr().err


def test_a_dropped_story_is_neither_a_piece_nor_left_to_forecast(board_env, capsys):
    assert cli.main(["epic", "forecast", "Permission rework", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["pieces"] == 2

    assert cli.main(["epic", "forecast", "Permission rework"]) == 0
    assert "  1 piece left" in capsys.readouterr().out.splitlines()


def _member(
    number: int, repo: str = REPO, closed_at: str | None = None, feature: str | None = "OPT_PERMISSION"
) -> dict:
    values = [{"name": "Done" if closed_at else "Backlog", "field": {"name": "Status"}}]
    if feature is not None:
        values.append({"name": "Permission rework", "optionId": feature, "field": {"name": "Feature"}})
    return {
        "id": f"I_{number}",
        "isArchived": False,
        "content": {
            "number": number,
            "title": "More",
            "url": f"https://github.com/{repo}/issues/{number}",
            "state": "CLOSED" if closed_at else "OPEN",
            "closedAt": closed_at,
            "body": "",
            "repository": {"nameWithOwner": repo},
            "labels": {"nodes": [{"name": "deckhand"}]},
            "comments": {"nodes": []},
        },
        "fieldValues": {"nodes": values},
    }


# #301 finished in the partial week work began in, so five finishes over the four full weeks to 2026-10-02: 2, 0, 2, 1.
PACED = [
    _member(310 + index, closed_at=f"{day}T12:00:00Z")
    for index, day in enumerate(["2026-09-10", "2026-09-10", "2026-09-24", "2026-09-24", "2026-10-01"])
]


def _with_members(tmp_path, monkeypatch, *nodes: dict) -> None:
    items = json.loads((FIXTURES / "epic-items.json").read_text(encoding="utf-8"))
    items["data"]["organization"]["projectV2"]["items"]["nodes"].extend(nodes)
    path = tmp_path / "more-items.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(path))


def test_forecast_counts_a_member_in_another_repository(board_env, monkeypatch, capsys):
    _with_members(board_env, monkeypatch, *PACED, _member(7, repo="acme/gadgets"))

    assert cli.main(["epic", "forecast", "Permission rework", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert (found["pieces"], found["done"], found["pace"]) == (8, 6, "epic")
    assert found["commitment"] > "2026-10-16"

    assert cli.main(["epic", "forecast", "Permission rework"]) == 0
    assert "  2 pieces left" in capsys.readouterr().out.splitlines()


def test_forecast_gives_no_dates_when_the_pace_would_take_over_two_years(board_env, monkeypatch, capsys):
    _with_members(board_env, monkeypatch, *PACED, *[_member(400 + index) for index in range(120)])

    assert cli.main(["epic", "forecast", "Permission rework", "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert (found["floor"], found["commitment"], found["worst"], found["pace"]) == (None, None, None, None)
    assert (found["pieces"], found["per_week"], found["reason"]) == (127, 1.25, "over two years")

    assert cli.main(["epic", "forecast", "Permission rework"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "## Forecast: Permission rework",
        "  121 pieces left",
        "",
        "  The measured pace would take over two years, so there is no date range.",
    ]


def test_two_forecasts_of_an_unchanged_board_give_the_same_dates(board_env, capsys, monkeypatch):
    seeds = []
    real = epic.throughput.outlook

    def _outlook(*args, **kwargs):
        seeds.append(kwargs.get("seed"))
        return real(*args, **kwargs)

    monkeypatch.setattr(epic.throughput, "outlook", _outlook)

    assert cli.main(["epic", "forecast", "Permission rework", "--json"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["epic", "forecast", "Permission rework", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == json.loads(first)
    assert seeds == [epic.SEED, epic.SEED]


def _archived_and_closed(tmp_path, monkeypatch) -> None:
    items = json.loads((FIXTURES / "epic-items.json").read_text(encoding="utf-8"))
    for node in items["data"]["organization"]["projectV2"]["items"]["nodes"]:
        if node["content"]["number"] == 302:
            node["content"].update(state="CLOSED", closedAt="2026-09-20T00:00:00Z")
        if node["content"]["number"] in (301, 302):
            node["isArchived"] = True
    path = tmp_path / "archived-feature.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(path))


def test_forecast_reads_a_finished_feature_the_board_archived(board_env, monkeypatch, capsys):
    _archived_and_closed(board_env, monkeypatch)

    assert cli.main(["epic", "forecast", "Permission rework", "--json"]) == 0

    found = json.loads(capsys.readouterr().out)
    assert (found["closed"], found["pieces"], found["done"]) == (True, 2, 2)
    assert (found["floor"], found["commitment"], found["worst"], found["pace"]) == (None, None, None, None)
