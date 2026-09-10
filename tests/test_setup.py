from __future__ import annotations

import json

import pytest

from tests.conftest import FIXTURES, run_deckhand


def _fields_file(tmp_path, name, fields):
    path = tmp_path / name
    path.write_text(json.dumps({"fields": fields, "totalCount": len(fields)}))
    return str(path)


STATUS = {
    "id": "PVTSSF_STATUS",
    "name": "Status",
    "type": "ProjectV2SingleSelectField",
    "options": [
        {"id": "o1", "name": "Backlog"},
        {"id": "o2", "name": "Blocked"},
        {"id": "o3", "name": "In Progress"},
        {"id": "o4", "name": "Pending Review"},
        {"id": "o5", "name": "Done"},
    ],
}
KIND = {"id": "PVTSSF_KIND", "name": "Kind", "type": "ProjectV2SingleSelectField", "options": []}
POINTS = {"id": "PVTF_POINTS", "name": "Story Points", "type": "ProjectV2Field"}
ACTUAL = {"id": "PVTF_ACTUAL", "name": "Actual", "type": "ProjectV2Field"}

REPO_EDIT = (
    "repo edit --enable-squash-merge --enable-merge-commit=false --enable-rebase-merge=false "
    "--squash-merge-commit-message pr-title-description --delete-branch-on-merge"
)
SLUG = "repo view --json nameWithOwner"
LINKED = "api graphql linked-projects"
FIELDS_QUERY = "api graphql project-fields"
VIEWS_QUERY = "api graphql project-views"
KIND_CREATE = "api graphql create-kind"
KIND_UPDATE = "api graphql update-kind"
MERGES = "api repos/acme/widgets"

# Every checklist item is done with the default fixtures; these Status options are the ones a
# fresh project has, so a test that wants something left to do reads them instead.
OLD_STATUS = [
    {"id": f"o{i}", "name": name, "color": "GRAY"}
    for i, name in enumerate(["Backlog", "Blocked", "In Progress", "Pending Review", "Done"], start=1)
]
KIND_COLORS = [
    ("feat", "GREEN"),
    ("fix", "RED"),
    ("chore", "GRAY"),
    ("refactor", "BLUE"),
    ("docs", "PURPLE"),
    ("perf", "ORANGE"),
]
KIND_OPTIONS = [{"name": name, "color": color, "description": ""} for name, color in KIND_COLORS]


def _short(call: str) -> str:
    """One recorded call, with each project query shortened to the name the assertions use."""
    if not call.startswith("api graphql"):
        return call
    if "createProjectV2Field" in call:
        return KIND_CREATE
    if "updateProjectV2Field" in call:
        return KIND_UPDATE
    if "views(" in call:
        return VIEWS_QUERY
    return FIELDS_QUERY if "fields(first" in call else LINKED


def _calls(gh_calls) -> list[str]:
    """The recorded gh calls, with the project queries shortened to their names."""
    return [_short(call) for call in gh_calls()]


def _fields_without(tmp_path, name) -> dict[str, str]:
    """The `fields.json` fixture with the field `name` dropped, as the env that points setup at it."""
    data = json.loads((FIXTURES / "fields.json").read_text(encoding="utf-8"))
    fields = [f for f in data["fields"] if f["name"] != name]
    return {"GH_FIELDS_FILE": _fields_file(tmp_path, f"without-{name.lower()}.json", fields)}


def _deletes(gh_calls) -> list[str]:
    return [call for call in gh_calls() if call.startswith("project field-delete")]


def _project_fields_file(tmp_path, name, nodes):
    path = tmp_path / name
    path.write_text(json.dumps({"data": {"organization": {"projectV2": {"fields": {"nodes": nodes}}}}}))
    return str(path)


def _project_fields(tmp_path, name, status=None, kind=None, drop=()) -> dict[str, str]:
    """The project-fields fixture with Status or Kind options replaced or a field dropped, as env."""
    data = json.loads((FIXTURES / "project-fields.json").read_text(encoding="utf-8"))
    nodes = []
    for node in data["data"]["organization"]["projectV2"]["fields"]["nodes"]:
        if node["name"] in drop:
            continue
        if node["name"] == "Status" and status is not None:
            node = {**node, "options": status}
        if node["name"] == "Kind" and kind is not None:
            node = {**node, "options": kind}
        nodes.append(node)
    return {"GH_PROJECT_FIELDS_FILE": _project_fields_file(tmp_path, name, nodes)}


def _views(tmp_path, name, views: dict[str, list[str]]) -> dict[str, str]:
    """A views answer showing `views` by name, as the env that points setup at it."""
    nodes = [{"name": view, "fields": {"nodes": [{"name": f} for f in shown]}} for view, shown in views.items()]
    path = tmp_path / name
    path.write_text(json.dumps({"data": {"organization": {"projectV2": {"views": {"nodes": nodes}}}}}))
    return {"GH_PROJECT_VIEWS_FILE": str(path)}


def _merges(tmp_path, name, **changes) -> dict[str, str]:
    """The repository settings fixture with `changes`, as the env that points setup at it."""
    data = json.loads((FIXTURES / "repo-settings.json").read_text(encoding="utf-8"))
    path = tmp_path / name
    path.write_text(json.dumps({**data, **changes}))
    return {"GH_REPO_SETTINGS_FILE": str(path)}


def _payload(call: str) -> dict:
    """The JSON document a recorded `api graphql --input -` call sent on stdin."""
    return json.loads(call.split(" ", 4)[4])


# --- the command surface ------------------------------------------------


def test_setup_has_no_detect_link_or_project_subcommand(repo):
    for removed in ("detect", "link", "project"):
        result = run_deckhand("setup", removed, cwd=repo)
        assert result.returncode == 2, removed
        assert "invalid choice" in result.stderr, removed


def test_setup_has_no_settings_or_templates_subcommand(repo):
    for removed in ("settings", "templates"):
        result = run_deckhand("setup", removed, cwd=repo)
        assert result.returncode == 2, removed


# --- context ------------------------------------------------------------


def test_context_reports_the_repository_the_links_and_the_fields(repo, fake_gh, gh_calls):
    result = run_deckhand("setup", "context", cwd=repo)

    assert result.returncode == 0
    out = result.stdout
    assert "Repository: acme/widgets" in out
    assert "Linked projects:\n  acme #2 Widgets\n" in out
    assert "Open projects for acme (repository owner):" in out
    assert "project list --owner acme --format json" in gh_calls()  # the label never reaches gh
    assert "2  Widgets" in out
    assert "Archive" not in out  # closed projects are filtered out
    assert "Project override: none (the linked project is used)" in out
    assert "Fields:\n  Kind: present\n  Story Points: present\n  Actual: present\n" in out


def test_context_reports_a_missing_field(repo, fake_gh, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_FIELDS_FILE", _fields_file(tmp_path, "partial.json", [STATUS, KIND]))

    result = run_deckhand("setup", "context", cwd=repo)

    assert result.returncode == 0
    assert "  Kind: present" in result.stdout
    assert "  Story Points: missing" in result.stdout
    assert "  Actual: missing" in result.stdout


def test_context_reports_a_field_of_the_wrong_type(repo, fake_gh, tmp_path, monkeypatch):
    wrong = [STATUS, {"id": "PVTF_KIND", "name": "Kind", "type": "ProjectV2Field"}, POINTS, ACTUAL]
    monkeypatch.setenv("GH_FIELDS_FILE", _fields_file(tmp_path, "wrong.json", wrong))

    result = run_deckhand("setup", "context", cwd=repo)

    assert result.returncode == 0
    assert "  Kind: wrong type (ProjectV2Field, expected SINGLE_SELECT)" in result.stdout


def test_context_reports_no_linked_projects_and_unknown_fields(repo, fake_gh):
    env = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-none.json")}

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert result.returncode == 0
    assert "Linked projects: none" in result.stdout
    assert "Fields: unknown (" in result.stdout


def test_context_lists_every_linked_project(repo, fake_gh):
    env = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-two.json")}

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert result.returncode == 0
    assert "Linked projects:\n  acme #2 Widgets\n  acme #7 Platform\n" in result.stdout


def test_context_honors_the_project_override_from_the_environment(repo, fake_gh):
    result = run_deckhand("setup", "context", cwd=repo, env={"DECKHAND_PROJECT": "7"})

    assert result.returncode == 0
    assert "Project override: 7 (DECKHAND_PROJECT in the environment)" in result.stdout


def test_context_honors_the_project_override_from_the_settings_file(repo, fake_gh):
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps({"env": {"DECKHAND_PROJECT": "2"}}))

    result = run_deckhand("setup", "context", cwd=repo)

    assert result.returncode == 0
    assert "Project override: 2 (" in result.stdout


def test_context_ignores_a_non_string_override_the_way_every_other_command_does(repo, fake_gh):
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps({"env": {"DECKHAND_PROJECT": True}}))

    result = run_deckhand("setup", "context", cwd=repo)

    assert result.returncode == 0
    assert "Project override: none (the linked project is used)" in result.stdout


def test_context_resolves_the_project_link_once(repo, fake_gh, gh_calls):
    result = run_deckhand("setup", "context", cwd=repo)

    assert result.returncode == 0
    assert len([c for c in gh_calls() if "projectsV2(first" in c]) == 1


def test_context_resolves_the_project_link_once_with_an_override(repo, fake_gh, gh_calls):
    env = {"DECKHAND_PROJECT": "2", "GH_LINKED_PROJECTS": str(FIXTURES / "linked-two.json")}

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert result.returncode == 0
    assert "Project override: 2" in result.stdout
    assert "  Kind: present" in result.stdout
    assert len([c for c in gh_calls() if "projectsV2(first" in c]) == 1


def test_context_reads_the_fields_of_a_user_owned_linked_project(repo, fake_gh, gh_calls):
    env = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-user.json")}

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert result.returncode == 0
    assert "Linked projects:\n  mjm #4 Personal\n" in result.stdout
    assert "Open projects for acme (repository owner):" in result.stdout
    assert any("project field-list 4 --owner mjm" in c for c in gh_calls())


def _plugins_file(tmp_path, name, data) -> dict[str, str]:
    """An `installed_plugins.json` of `data`, as the env that points setup at it."""
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return {"DECKHAND_PLUGINS_FILE": str(path)}


INSTALLED = {"version": 2, "plugins": {"superpowers@superpowers-marketplace": [{"scope": "user"}]}}


def _checklist(result) -> list[str]:
    """The lines under `Checklist:`, up to the superpowers line."""
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    start = lines.index("Checklist:")
    return lines[start + 1 : -1]


def test_context_reports_each_checklist_item(repo, fake_gh, tmp_path):
    env = {
        **_project_fields(tmp_path, "old-status.json", status=OLD_STATUS),
        **_plugins_file(tmp_path, "installed.json", INSTALLED),
    }

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert _checklist(result) == [
        "  Status options: set to Draft, Backlog, In Progress, Pending Review, Done "
        "(currently: Backlog, Blocked, In Progress, Pending Review, Done)",
        "  Board view fields: done",
        "  Kind colors: done",
        "  Merge settings: done",
    ]
    assert result.stdout.splitlines()[-1] == "superpowers: installed"


def test_context_reports_every_checklist_item_as_done_with_a_finished_project(repo, fake_gh):
    result = run_deckhand("setup", "context", cwd=repo)

    assert _checklist(result) == [
        "  Status options: done",
        "  Board view fields: done",
        "  Kind colors: done",
        "  Merge settings: done",
    ]


def test_context_reports_the_kind_colors_that_are_off(repo, fake_gh, tmp_path):
    kind = [{"id": "opt_feat", "name": "feat", "color": "GREEN"}, {"id": "opt_fix", "name": "fix", "color": "BLUE"}]
    env = _project_fields(tmp_path, "kind-off.json", kind=kind)

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert (
        "  Kind colors: set fix red, add chore gray, add refactor blue, add docs purple, add perf orange"
        in _checklist(result)
    )


def test_context_reports_a_missing_kind_as_apply_work(repo, fake_gh, tmp_path):
    env = _project_fields(tmp_path, "no-kind.json", drop=("Kind",))

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert "  Kind colors: Kind is missing; apply creates it" in _checklist(result)


def test_context_reports_the_board_view_fields_that_are_off(repo, fake_gh, tmp_path):
    env = _views(tmp_path, "narrow.json", {"Board": ["Title", "Status"], "Table": ["Title"]})

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert "  Board view fields: on the Board view turn on Kind, Story Points, Actual, Assignees, Repository" in (
        _checklist(result)
    )


def test_context_reports_a_project_with_no_board_view(repo, fake_gh, tmp_path):
    env = _views(tmp_path, "table-only.json", {"Table": ["Title"]})

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert "  Board view fields: no view named Board" in _checklist(result)


def test_context_reports_the_merge_settings_that_are_off(repo, fake_gh, tmp_path):
    env = _merges(tmp_path, "merges-off.json", allow_merge_commit=True, squash_merge_commit_message="COMMIT_MESSAGES")

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert (
        "  Merge settings: make squash the only merge, with the pull request body as its message, and delete the "
        "branch on merge (currently: allow_merge_commit true, squash_merge_commit_message commit_messages)"
    ) in _checklist(result)


def test_context_reports_each_item_it_cannot_read(repo, fake_gh, tmp_path):
    env = {
        "GH_PROJECT_FIELDS_FILE": str(tmp_path / "gone-fields.json"),
        "GH_PROJECT_VIEWS_FILE": str(tmp_path / "gone-views.json"),
        "GH_REPO_SETTINGS_FILE": str(tmp_path / "gone-merges.json"),
    }

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    lines = _checklist(result)
    assert len(lines) == 4
    for name, line in zip(["Status options", "Board view fields", "Kind colors", "Merge settings"], lines, strict=True):
        assert line.startswith(f"  {name}: unknown ("), line


def test_context_reports_superpowers_installed(repo, fake_gh, tmp_path):
    env = _plugins_file(tmp_path, "installed.json", INSTALLED)

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert result.returncode == 0
    assert "superpowers: installed" in result.stdout


def test_context_reads_a_plugin_map_at_the_top_level(repo, fake_gh, tmp_path):
    """Older files are the map itself rather than a `plugins` key over it."""
    env = _plugins_file(tmp_path, "flat.json", {"superpowers@sp": [{"scope": "user"}]})

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert result.returncode == 0
    assert "superpowers: installed" in result.stdout


def test_context_ignores_the_top_level_when_a_plugins_key_is_there(repo, fake_gh, tmp_path):
    """A `plugins` key that is not a map is a file this cannot read, not a reason to read around it."""
    env = _plugins_file(tmp_path, "wrong-shape.json", {"plugins": ["superpowers@sp"], "superpowers@sp": []})

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert result.returncode == 0
    assert "superpowers: unknown (no plugin list in the file)" in result.stdout


def test_context_reports_superpowers_unknown_when_the_file_is_not_a_map(repo, fake_gh, tmp_path):
    """Valid JSON that is not an object holds no plugin list at either shape."""
    env = _plugins_file(tmp_path, "list.json", ["superpowers@sp"])

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert result.returncode == 0
    assert "superpowers: unknown (no plugin list in the file)" in result.stdout


def test_context_reports_superpowers_missing(repo, fake_gh, tmp_path):
    env = _plugins_file(tmp_path, "other.json", {"plugins": {"deckhand@jsapartners": [{"scope": "user"}]}})

    result = run_deckhand("setup", "context", cwd=repo, env=env)

    assert result.returncode == 0
    assert "superpowers: not found; install it first" in result.stdout


def test_context_reports_superpowers_unknown_when_the_file_is_not_there(repo, fake_gh, tmp_path):
    result = run_deckhand("setup", "context", cwd=repo, env={"DECKHAND_PLUGINS_FILE": str(tmp_path / "gone.json")})

    assert result.returncode == 0
    assert "superpowers: unknown (" in result.stdout


def test_context_reports_superpowers_unknown_when_the_file_is_not_json(repo, fake_gh, tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{")

    result = run_deckhand("setup", "context", cwd=repo, env={"DECKHAND_PLUGINS_FILE": str(path)})

    assert result.returncode == 0
    assert "superpowers: unknown (" in result.stdout


def test_context_reports_no_open_projects_when_every_project_is_closed(repo, fake_gh, tmp_path):
    listing = tmp_path / "closed.json"
    listing.write_text(json.dumps({"projects": [{"number": 9, "title": "Archive", "closed": True}]}))

    result = run_deckhand("setup", "context", cwd=repo, env={"GH_PROJECT_LIST_FILE": str(listing)})

    assert result.returncode == 0
    assert "Open projects for acme (repository owner): none" in result.stdout
    assert "Archive" not in result.stdout


def test_context_reports_a_project_listing_it_cannot_read(repo, fake_gh, tmp_path):
    listing = tmp_path / "broken.json"
    listing.write_text("{not json")

    result = run_deckhand("setup", "context", cwd=repo, env={"GH_PROJECT_LIST_FILE": str(listing)})

    assert result.returncode == 0
    assert "Open projects for acme (repository owner): unknown (" in result.stdout


def test_context_never_exits_non_zero_without_gh(repo):
    # No fake_gh fixture here: the autouse no_real_gh stub fails every gh call loudly.
    result = run_deckhand("setup", "context", cwd=repo)

    assert result.returncode == 0
    assert "Repository: unknown" in result.stdout


def test_context_reports_a_settings_file_it_cannot_read_on_one_line(repo):
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("{not json")

    result = run_deckhand("setup", "context", cwd=repo)

    assert result.returncode == 0
    assert result.stdout.startswith("(deckhand setup context failed: ")
    assert "is not valid JSON" in result.stdout


# --- apply --------------------------------------------------------------


def test_apply_links_when_given_a_project(repo, fake_gh, gh_calls, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_FIELDS_FILE", _fields_file(tmp_path, "partial.json", [STATUS, KIND, ACTUAL]))

    result = run_deckhand("setup", "apply", "--project", "5", cwd=repo)

    assert result.returncode == 0, result.stderr
    assert _calls(gh_calls) == [
        SLUG,
        LINKED,
        "project link 5 --owner acme --repo acme/widgets",
        LINKED,
        "project view 5 --owner acme --format json",
        REPO_EDIT,
        "project field-list 5 --owner acme --format json",
        "project field-create 5 --owner acme --name Story Points --data-type NUMBER",
        FIELDS_QUERY,
        "project field-delete --id PVTSSF_PRIORITY",
        VIEWS_QUERY,
        MERGES,
    ]
    assert "linked acme #5 to acme/widgets" in result.stdout
    assert "merge: squash only, message from the pull request" in result.stdout


def test_apply_accepts_an_explicit_owner(repo, fake_gh, gh_calls):
    env = {
        "GH_LINKED_PROJECTS": str(FIXTURES / "linked-none.json"),
        "GH_PROJECT_OWNER": "mjm",
        "GH_PROJECT_OWNER_TYPE": "User",
        "GH_PROJECT_FIELDS_FILE": str(FIXTURES / "project-fields-user.json"),
    }

    result = run_deckhand("setup", "apply", "--project", "4", "--owner", "@me", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    assert _calls(gh_calls) == [
        SLUG,
        LINKED,
        "project link 4 --owner @me --repo acme/widgets",
        LINKED,
        "project view 4 --owner acme --format json",
        REPO_EDIT,
        "project field-list 4 --owner mjm --format json",
        FIELDS_QUERY,
        "project field-delete --id PVTSSF_PRIORITY",
        KIND_UPDATE,
        VIEWS_QUERY,
        MERGES,
    ]
    assert "linked @me #4 to acme/widgets" in result.stdout
    assert "Kind: recolored" in result.stdout


def test_apply_targets_the_named_project_when_another_is_linked(repo, fake_gh, gh_calls):
    # Two projects are linked, which refuses without a number; the one named here is neither.
    env = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-two.json")}

    result = run_deckhand("setup", "apply", "--project", "9", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    assert _calls(gh_calls) == [
        SLUG,
        LINKED,
        "project link 9 --owner acme --repo acme/widgets",
        LINKED,
        "project view 9 --owner acme --format json",
        REPO_EDIT,
        "project field-list 9 --owner acme --format json",
        FIELDS_QUERY,
        "project field-delete --id PVTSSF_PRIORITY",
        VIEWS_QUERY,
        MERGES,
    ]


def test_apply_skips_the_link_when_the_named_project_is_already_linked(repo, fake_gh, gh_calls):
    env = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-two.json")}

    result = run_deckhand("setup", "apply", "--project", "7", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    assert _calls(gh_calls) == [
        SLUG,
        LINKED,
        REPO_EDIT,
        "project field-list 7 --owner acme --format json",
        FIELDS_QUERY,
        "project field-delete --id PVTSSF_PRIORITY",
        VIEWS_QUERY,
        MERGES,
    ]
    assert "linked" not in result.stdout


def test_apply_without_a_project_uses_the_linked_one(repo, fake_gh, gh_calls):
    result = run_deckhand("setup", "apply", cwd=repo)

    assert result.returncode == 0, result.stderr
    assert _calls(gh_calls) == [
        SLUG,
        LINKED,
        REPO_EDIT,
        "project field-list 2 --owner acme --format json",
        FIELDS_QUERY,
        "project field-delete --id PVTSSF_PRIORITY",
        VIEWS_QUERY,
        MERGES,
    ]
    assert "linked" not in result.stdout


def test_apply_refuses_without_a_linked_project(repo, fake_gh, gh_calls):
    env = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-none.json")}

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand setup apply:")
    assert "--project N" in result.stderr
    assert not [c for c in gh_calls() if c.startswith("repo edit")]
    assert not [c for c in gh_calls() if c.startswith("project field-create")]
    assert _deletes(gh_calls) == []


def test_apply_refuses_with_several_linked_projects_and_no_override(repo, fake_gh, gh_calls):
    env = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-two.json")}

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand setup apply:")
    assert "DECKHAND_PROJECT" in result.stderr
    assert not [c for c in gh_calls() if c.startswith("repo edit")]
    assert not [c for c in gh_calls() if c.startswith("project field-create")]
    assert _deletes(gh_calls) == []


def test_apply_honors_the_override(repo, fake_gh, gh_calls):
    env = {"DECKHAND_PROJECT": "2", "GH_LINKED_PROJECTS": str(FIXTURES / "linked-two.json")}

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    assert not [c for c in gh_calls() if c.startswith("project link")]
    assert REPO_EDIT in gh_calls()
    assert "project field-list 2 --owner acme --format json" in gh_calls()


def test_apply_reports_a_failed_link_and_writes_nothing_else(repo, fake_gh, gh_calls):
    env = {"GH_PROJECT_LINK_FAILS": "1"}

    result = run_deckhand("setup", "apply", "--project", "5", cwd=repo, env=env)

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand setup apply:")
    assert "could not link project" in result.stderr
    assert _calls(gh_calls) == [SLUG, LINKED, "project link 5 --owner acme --repo acme/widgets"]


def test_apply_reports_a_failed_repo_edit_and_creates_no_field(repo, fake_gh, gh_calls, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_FIELDS_FILE", _fields_file(tmp_path, "partial.json", [STATUS]))
    monkeypatch.setenv("GH_REPO_EDIT_FAILS", "1")

    result = run_deckhand("setup", "apply", cwd=repo)

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand setup apply:")
    assert "repository settings could not be updated" in result.stderr
    assert not [c for c in gh_calls() if c.startswith("project field-create")]


def test_apply_rejects_a_non_numeric_project(repo, fake_gh):
    result = run_deckhand("setup", "apply", "--project", "abc", cwd=repo)

    assert result.returncode == 2
    assert "project number" in result.stderr


def test_apply_fails_loudly_without_gh(repo):
    # No fake_gh fixture here: unlike context, apply says so rather than carrying on.
    result = run_deckhand("setup", "apply", cwd=repo)

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand setup:")


# --- apply: the project fields ------------------------------------------


def test_apply_creates_only_missing_fields_and_prints_the_checklist(repo, fake_gh, gh_calls, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_FIELDS_FILE", _fields_file(tmp_path, "partial.json", [STATUS, KIND, ACTUAL]))

    result = run_deckhand(
        "setup", "apply", cwd=repo, env=_project_fields(tmp_path, "old-status.json", status=OLD_STATUS)
    )

    assert result.returncode == 0, result.stderr
    created = [c for c in gh_calls() if c.startswith("project field-create")]
    assert len(created) == 1
    assert "--name Story Points" in created[0]
    assert "Kind: already present" in result.stdout
    assert "Actual: already present" in result.stdout
    assert "1. Status options:" in result.stdout


def test_apply_creates_kind_with_colored_options(repo, fake_gh, gh_calls, tmp_path):
    env = _fields_without(tmp_path, "Kind")

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    (call,) = [c for c in gh_calls() if "createProjectV2Field" in c]
    assert call.startswith("api graphql --input -")
    payload = _payload(call)
    assert "createProjectV2Field(input:{projectId:$project,dataType:SINGLE_SELECT,name:$name," in payload["query"]
    assert payload["variables"] == {"project": "PVT_TEST", "name": "Kind", "options": KIND_OPTIONS}
    assert not any(c.startswith("project field-create") for c in gh_calls())
    assert "Kind: created" in result.stdout


def test_apply_recolors_a_kind_whose_options_are_off(repo, fake_gh, gh_calls, tmp_path):
    kind = [
        {"id": "opt_feat", "name": "feat", "color": "GREEN"},
        {"id": "opt_fix", "name": "fix", "color": "BLUE"},
        {"id": "opt_spike", "name": "spike", "color": "PINK"},
    ]
    env = _project_fields(tmp_path, "kind-off.json", kind=kind)

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    (call,) = [c for c in gh_calls() if "updateProjectV2Field" in c]
    payload = _payload(call)
    assert "updateProjectV2Field(input:{fieldId:$field,singleSelectOptions:$options})" in payload["query"]
    assert payload["variables"] == {
        "field": "PVTSSF_KIND",
        "options": [
            {"id": "opt_feat", "name": "feat", "color": "GREEN", "description": ""},
            {"id": "opt_fix", "name": "fix", "color": "RED", "description": ""},
            {"name": "chore", "color": "GRAY", "description": ""},
            {"name": "refactor", "color": "BLUE", "description": ""},
            {"name": "docs", "color": "PURPLE", "description": ""},
            {"name": "perf", "color": "ORANGE", "description": ""},
            {"id": "opt_spike", "name": "spike", "color": "PINK", "description": ""},
        ],
    }
    lines = result.stdout.splitlines()
    assert "Kind: recolored" in lines
    assert lines[-1] == "Setup complete."


def test_apply_leaves_a_kind_whose_options_are_right_alone(repo, fake_gh, gh_calls):
    result = run_deckhand("setup", "apply", cwd=repo)

    assert result.returncode == 0, result.stderr
    assert not any("updateProjectV2Field" in c for c in gh_calls())
    assert "recolored" not in result.stdout


def test_apply_ends_with_setup_complete_when_nothing_is_left(repo, fake_gh):
    result = run_deckhand("setup", "apply", cwd=repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "Setup complete."


def test_apply_names_what_is_left_with_the_page_and_the_click(repo, fake_gh, tmp_path):
    env = _project_fields(tmp_path, "old-status.json", status=OLD_STATUS)

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    start = lines.index("Finish by hand (the API cannot do this):")
    assert lines[start - 1] == ""
    assert lines[start + 1 :] == [
        "  1. Status options: set to Draft, Backlog, In Progress, Pending Review, Done "
        "(currently: Backlog, Blocked, In Progress, Pending Review, Done). Project > Settings > Status. "
        "Deleting an option is permanent.",
        "Run setup again when done; it says what is still left.",
    ]
    assert not any("Issue Types" in line or "issue-types" in line for line in lines)


def test_apply_names_the_click_for_the_board_view(repo, fake_gh, tmp_path):
    env = _views(tmp_path, "narrow.json", {"Board": ["Title", "Status"]})

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    assert (
        "  1. Board view fields: on the Board view turn on Kind, Story Points, Actual, Assignees, Repository. "
        "Board view > view menu > Fields."
    ) in result.stdout.splitlines()


def test_apply_names_a_missing_board_view(repo, fake_gh, tmp_path):
    env = _views(tmp_path, "table-only.json", {"Table": ["Title"]})

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    assert "  1. Board view fields: no view named Board. Board view > view menu > Fields." in result.stdout.splitlines()


def test_apply_lists_what_it_could_not_read_apart_from_the_work(repo, fake_gh, tmp_path):
    env = {
        **_project_fields(tmp_path, "old-status.json", status=OLD_STATUS),
        "GH_PROJECT_VIEWS_FILE": str(tmp_path / "gone-views.json"),
    }

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("Finish by hand (the API cannot do this):") + 1].startswith("  1. Status options:")
    start = lines.index("Could not read:")
    assert lines[start + 1].startswith("  Board view fields: unknown (")
    assert lines[start + 2] == "Run setup again when done; it says what is still left."
    assert not any(line.startswith("  2.") for line in lines)


def test_apply_lists_only_what_it_could_not_read_when_nothing_else_is_left(repo, fake_gh, tmp_path):
    env = {"GH_REPO_SETTINGS_FILE": str(tmp_path / "gone-merges.json")}

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "Finish by hand (the API cannot do this):" not in lines
    assert "Setup complete." not in lines
    assert lines[lines.index("Could not read:") + 1].startswith("  Merge settings: unknown (")
    assert lines[-1] == "Run setup again when done; it says what is still left."


def test_apply_creates_story_points_as_a_number_field(repo, fake_gh, gh_calls, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_FIELDS_FILE", _fields_file(tmp_path, "no-points.json", [STATUS, KIND, ACTUAL]))

    result = run_deckhand("setup", "apply", cwd=repo)

    assert result.returncode == 0, result.stderr
    created = [c for c in gh_calls() if c.startswith("project field-create")]
    assert len(created) == 1
    assert "--data-type NUMBER" in created[0]


def test_apply_creates_no_date_fields(repo, fake_gh, gh_calls, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_FIELDS_FILE", _fields_file(tmp_path, "status-only.json", [STATUS]))

    result = run_deckhand("setup", "apply", cwd=repo)

    assert result.returncode == 0, result.stderr
    created = [c for c in gh_calls() if c.startswith("project field-create") or "createProjectV2Field" in c]
    assert len(created) == 3
    for gone in ("Started", "Finished", "Active Days"):
        assert not any(gone in c for c in created), gone
        assert gone not in result.stdout


def test_apply_reports_a_field_of_the_wrong_type_and_leaves_it_alone(repo, fake_gh, gh_calls, tmp_path, monkeypatch):
    wrong = [
        {"id": "PVTSSF_STATUS", "name": "Status", "type": "ProjectV2SingleSelectField"},
        {"id": "PVTF_KIND", "name": "Kind", "type": "ProjectV2Field"},
        POINTS,
        ACTUAL,
    ]
    monkeypatch.setenv("GH_FIELDS_FILE", _fields_file(tmp_path, "wrong.json", wrong))

    result = run_deckhand("setup", "apply", cwd=repo)

    assert result.returncode == 0, result.stderr
    assert "Kind: present but is ProjectV2Field, expected SINGLE_SELECT (fix by hand)" in result.stdout
    assert "Kind: already present" not in result.stdout
    assert not [c for c in gh_calls() if c.startswith("project field-create")]
    assert not [c for c in gh_calls() if "createProjectV2Field" in c]


def test_apply_names_the_project_it_resolved_before_it_writes(repo, fake_gh):
    """Every later line is about one project, so the run says which one before it changes anything."""
    result = run_deckhand("setup", "apply", cwd=repo)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "Project: acme #2" in lines
    assert lines.index("Project: acme #2") < lines.index("merge: squash only, message from the pull request")


# --- apply: the fields the process did not make -------------------------


def test_apply_deletes_a_template_field_after_the_creates_and_before_the_checklist(repo, fake_gh, gh_calls):
    result = run_deckhand("setup", "apply", cwd=repo)

    assert result.returncode == 0, result.stderr
    assert _deletes(gh_calls) == ["project field-delete --id PVTSSF_PRIORITY"]
    lines = result.stdout.splitlines()
    assert "deleted field Priority" in lines
    assert lines.index("Kind: already present") < lines.index("deleted field Priority")
    assert lines.index("deleted field Priority") < lines.index("Setup complete.")


def test_apply_deletes_nothing_when_the_project_has_only_the_fields_the_process_uses(repo, fake_gh, gh_calls, tmp_path):
    nodes = [
        {"id": "PVTF_TITLE", "name": "Title", "dataType": "TITLE"},
        {"id": "PVTSSF_STATUS", "name": "Status", "dataType": "SINGLE_SELECT"},
        {"id": "PVTSSF_KIND", "name": "Kind", "dataType": "SINGLE_SELECT"},
        {"id": "PVTF_POINTS", "name": "Story Points", "dataType": "NUMBER"},
        {"id": "PVTF_ACTUAL", "name": "Actual", "dataType": "NUMBER"},
    ]
    env = {"GH_PROJECT_FIELDS_FILE": _project_fields_file(tmp_path, "no-extras.json", nodes)}

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    assert _deletes(gh_calls) == []
    assert "deleted field" not in result.stdout


def test_apply_never_deletes_a_built_in_whose_name_looks_deletable(repo, fake_gh, gh_calls, tmp_path):
    nodes = [
        {"id": "PVTSSF_STATUS", "name": "Status", "dataType": "SINGLE_SELECT"},
        {"id": "PVTF_MILESTONE", "name": "Milestone", "dataType": "MILESTONE"},
        {"id": "PVTF_PARENT", "name": "Parent issue", "dataType": "PARENT_ISSUE"},
        {"id": "PVTF_PROGRESS", "name": "Sub-issues progress", "dataType": "SUB_ISSUES_PROGRESS"},
        {"id": "PVTF_TRACKS", "name": "Tracks", "dataType": "TRACKS"},
        {"id": "PVTF_CLOSED", "name": "Closed", "dataType": "CLOSED"},
    ]
    env = {"GH_PROJECT_FIELDS_FILE": _project_fields_file(tmp_path, "built-ins.json", nodes)}

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    assert _deletes(gh_calls) == []


def test_apply_never_deletes_status_even_beside_other_single_selects(repo, fake_gh, gh_calls, tmp_path):
    nodes = [
        {"id": "PVTSSF_STATUS", "name": "Status", "dataType": "SINGLE_SELECT"},
        {"id": "PVTSSF_PRIORITY", "name": "Priority", "dataType": "SINGLE_SELECT"},
        {"id": "PVTF_SIZE", "name": "Size", "dataType": "TEXT"},
        {"id": "PVTIF_SPRINT", "name": "Sprint", "dataType": "ITERATION"},
        {"id": "PVTF_DUE", "name": "Due", "dataType": "DATE"},
    ]
    env = {"GH_PROJECT_FIELDS_FILE": _project_fields_file(tmp_path, "many.json", nodes)}

    result = run_deckhand("setup", "apply", cwd=repo, env=env)

    assert result.returncode == 0, result.stderr
    assert _deletes(gh_calls) == [
        "project field-delete --id PVTSSF_PRIORITY",
        "project field-delete --id PVTF_SIZE",
        "project field-delete --id PVTIF_SPRINT",
        "project field-delete --id PVTF_DUE",
    ]
    assert "PVTSSF_STATUS" not in result.stdout


def test_context_deletes_nothing(repo, fake_gh, gh_calls):
    result = run_deckhand("setup", "context", cwd=repo)

    assert result.returncode == 0
    assert _deletes(gh_calls) == []


@pytest.mark.parametrize("verb", ["context", "apply"])
def test_setup_takes_no_issue_number(repo, fake_gh, verb):
    result = run_deckhand("setup", verb, "248", cwd=repo)

    assert result.returncode == 2
