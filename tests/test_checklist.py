from __future__ import annotations

import json

from deckhand import checklist, gh
from deckhand.checklist import Item
from tests.conftest import FIXTURES

REPO = "acme/widgets"
NAMES = ["Status options", "Board view fields", "Kind colors", "Merge settings"]


def _fields(**changes) -> list[dict]:
    """The project fields as `gh.project_fields` returns them from the fixture, with `changes` by name."""
    data = json.loads((FIXTURES / "project-fields.json").read_text(encoding="utf-8"))
    nodes = data["data"]["organization"]["projectV2"]["fields"]["nodes"]
    fields = []
    for node in nodes:
        if node["name"] in changes and changes[node["name"]] is None:
            continue
        fields.append({**node, "options": node.get("options") or [], **changes.get(node["name"], {})})
    return fields


def _views(tmp_path, name, views: dict[str, list[str]], monkeypatch) -> None:
    nodes = [{"name": view, "fields": {"nodes": [{"name": f} for f in shown]}} for view, shown in views.items()]
    path = tmp_path / name
    path.write_text(json.dumps({"data": {"organization": {"projectV2": {"views": {"nodes": nodes}}}}}))
    monkeypatch.setenv("GH_PROJECT_VIEWS_FILE", str(path))


def _merges(tmp_path, name, monkeypatch, **changes) -> None:
    data = json.loads((FIXTURES / "repo-settings.json").read_text(encoding="utf-8"))
    path = tmp_path / name
    path.write_text(json.dumps({**data, **changes}))
    monkeypatch.setenv("GH_REPO_SETTINGS_FILE", str(path))


def _item(items: list[Item], name: str) -> Item:
    return next(item for item in items if item.name == name)


# --- the Kind options ---------------------------------------------------


def test_kind_options_colors_each_kind_and_yellow_for_the_rest():
    options = checklist.kind_options(["feat", "fix", "spike"])

    assert options == [
        {"name": "feat", "color": "GREEN", "description": ""},
        {"name": "fix", "color": "RED", "description": ""},
        {"name": "spike", "color": "YELLOW", "description": ""},
    ]


def test_kind_update_is_none_when_every_kind_has_its_color():
    kind = next(f for f in _fields() if f["name"] == "Kind")

    assert checklist.kind_update(kind, ["feat", "fix", "chore", "refactor", "docs", "perf"]) is None


def test_kind_update_carries_every_option_by_id_and_keeps_what_a_person_added():
    kind = {
        "id": "PVTSSF_KIND",
        "name": "Kind",
        "dataType": "SINGLE_SELECT",
        "options": [
            {"id": "opt_fix", "name": "fix", "color": "BLUE"},
            {"id": "opt_spike", "name": "spike", "color": "PINK"},
            {"id": "opt_feat", "name": "feat", "color": "GREEN"},
        ],
    }

    assert checklist.kind_update(kind, ["feat", "fix", "docs"]) == [
        {"id": "opt_feat", "name": "feat", "color": "GREEN", "description": ""},
        {"id": "opt_fix", "name": "fix", "color": "RED", "description": ""},
        {"name": "docs", "color": "PURPLE", "description": ""},
        {"id": "opt_spike", "name": "spike", "color": "PINK", "description": ""},
    ]


# --- the items ----------------------------------------------------------


def test_every_item_is_done_with_the_fixtures(fake_gh, settings):
    items = checklist.checklist(settings, REPO, gh.project_fields(settings))

    assert [item.name for item in items] == NAMES
    assert [item.left for item in items] == [None, None, None, None]
    assert not any(item.unknown for item in items)


def test_each_item_names_its_click(fake_gh, settings):
    items = checklist.checklist(settings, REPO, _fields())

    assert [item.click for item in items] == [
        "Project > Settings > Status",
        "Board view > view menu > Fields",
        "Project > Settings > Kind",
        "Repository > Settings > General > Pull Requests",
    ]


def test_status_options_left_names_the_current_ones(fake_gh, settings):
    status = {
        "options": [{"id": "o1", "name": "Backlog", "color": "GRAY"}, {"id": "o2", "name": "Done", "color": "GRAY"}]
    }

    item = _item(checklist.checklist(settings, REPO, _fields(Status=status)), "Status options")

    assert item.left == "set to Draft, Backlog, In Progress, Pending Review, Done (currently: Backlog, Done)"


def test_status_options_left_says_none_when_status_has_no_options(fake_gh, settings):
    item = _item(checklist.checklist(settings, REPO, _fields(Status={"options": []})), "Status options")

    assert item.left is not None and item.left.endswith("(currently: none)")


def test_the_field_items_are_unknown_when_the_fields_were_not_read(fake_gh, settings):
    items = checklist.checklist(settings, REPO, None, "could not resolve to a Project")

    assert _item(items, "Status options").left == "unknown (could not resolve to a Project)"
    assert _item(items, "Kind colors").left == "unknown (could not resolve to a Project)"
    assert _item(items, "Status options").unknown and _item(items, "Kind colors").unknown
    assert _item(items, "Board view fields").left is None
    assert _item(items, "Merge settings").left is None


def test_board_view_left_names_what_it_shows(fake_gh, settings, tmp_path, monkeypatch):
    _views(tmp_path, "narrow.json", {"Board": ["Title", "Status"]}, monkeypatch)

    item = _item(checklist.checklist(settings, REPO, _fields()), "Board view fields")

    assert item.left == (
        "on the Board view show only Title, Status, Kind, Story Points, Actual, Assignees, Repository "
        "(currently: Title, Status)"
    )


def test_board_view_left_says_there_is_no_board_view(fake_gh, settings, tmp_path, monkeypatch):
    _views(tmp_path, "table-only.json", {"Table": ["Title"]}, monkeypatch)

    item = _item(checklist.checklist(settings, REPO, _fields()), "Board view fields")

    assert item.left == "no view named Board"


def test_board_view_is_unknown_when_the_views_cannot_be_read(fake_gh, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_PROJECT_VIEWS_FILE", str(tmp_path / "gone.json"))

    item = _item(checklist.checklist(settings, REPO, _fields()), "Board view fields")

    assert item.unknown and item.left.startswith("unknown (")


def test_kind_colors_left_says_which_to_set_and_which_to_add(fake_gh, settings):
    kind = {
        "options": [
            {"id": "opt_feat", "name": "feat", "color": "GREEN"},
            {"id": "opt_fix", "name": "fix", "color": "BLUE"},
        ]
    }

    item = _item(checklist.checklist(settings, REPO, _fields(Kind=kind)), "Kind colors")

    assert item.left == "set fix red, add chore gray, add refactor blue, add docs purple, add perf orange"


def test_kind_colors_left_says_a_missing_kind_is_apply_work(fake_gh, settings):
    item = _item(checklist.checklist(settings, REPO, _fields(Kind=None)), "Kind colors")

    assert item.left == "Kind is missing; apply creates it"
    assert not item.unknown


def test_kind_colors_left_says_a_kind_of_the_wrong_type(fake_gh, settings):
    item = _item(checklist.checklist(settings, REPO, _fields(Kind={"dataType": "NUMBER"})), "Kind colors")

    assert item.left == "Kind is NUMBER, not a single select"


def test_merge_settings_left_names_what_is_off(fake_gh, settings, tmp_path, monkeypatch):
    _merges(tmp_path, "off.json", monkeypatch, allow_rebase_merge=True, delete_branch_on_merge=False)

    item = _item(checklist.checklist(settings, REPO, _fields()), "Merge settings")

    assert item.left == (
        "make squash the only merge, with the pull request body as its message, and delete the branch on merge "
        "(currently: allow_rebase_merge true, delete_branch_on_merge false)"
    )


def test_merge_settings_are_unknown_without_a_repository(fake_gh, settings):
    item = _item(checklist.checklist(settings, None, _fields()), "Merge settings")

    assert item.left == "unknown (no repository)"
    assert item.unknown


def test_merge_settings_are_unknown_when_the_repository_cannot_be_read(fake_gh, settings, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_REPO_SETTINGS_FILE", str(tmp_path / "gone.json"))

    item = _item(checklist.checklist(settings, REPO, _fields()), "Merge settings")

    assert item.unknown and item.left.startswith("unknown (")
