"""Setup: report what a repository has, then link its project, set its merges, and fix its fields.

Nothing here writes into a repository's files. The project comes from the GitHub project link, the
merge settings come from `gh repo edit`, the board's three fields come from the API, and everything
the API cannot do is read back through `checklist` and printed for a person to finish by hand.

A project keeps exactly the three fields this module creates and GitHub's own Status. Whatever else
a person or a template added is deleted, because a field the process does not set is a column nobody
fills in; GitHub's own built-in fields cannot be deleted and are never touched.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from deckhand import checklist, config, gh
from deckhand.checklist import Item
from deckhand.config import SETTINGS_FILE, Settings
from deckhand.step import Refusal, reason, step

PROJECT_FIELDS: list[tuple[str, str]] = [
    ("Kind", "SINGLE_SELECT"),
    ("Story Points", "NUMBER"),
    ("Actual", "NUMBER"),
]

# What `gh project field-list` reports as `type` for each `field-create --data-type`.
FIELD_TYPES = {"SINGLE_SELECT": "ProjectV2SingleSelectField", "NUMBER": "ProjectV2Field"}

# Where Claude Code records the plugins it installed. deckhand does not declare superpowers as a
# dependency, so the one thing setup can do about it is say whether it is there.
PLUGINS_FILE = Path(".claude") / "plugins" / "installed_plugins.json"
SUPERPOWERS = "superpowers@"
NO_PLUGIN_LIST = "superpowers: unknown (no plugin list in the file)"

# The fields the process reads; every other field a person could have made is deleted. Status is
# GitHub's own, and a single select like any other, so it is kept by name.
KEPT_FIELDS = frozenset({"Status", *(name for name, _ in PROJECT_FIELDS)})
# The GraphQL data types `gh project field-create` can make, which are the ones it can delete.
DELETABLE_TYPES = frozenset({"TEXT", "NUMBER", "DATE", "SINGLE_SELECT", "ITERATION"})

# A story lands on main as one squash commit, and the pull request deckhand computes is its message.
MERGE_SETTINGS = (
    "repo",
    "edit",
    "--enable-squash-merge",
    "--enable-merge-commit=false",
    "--enable-rebase-merge=false",
    "--squash-merge-commit-message",
    "pr-title-description",
    "--delete-branch-on-merge",
)


def _project_number(value: str) -> str:
    """An argparse `type=` that reports a friendly message for a non-numeric project number."""
    if not (value.isascii() and value.isdigit()):
        raise argparse.ArgumentTypeError(f"project number must be an integer, got {value!r}")
    return value


def _settings_path() -> Path:
    return Path.cwd() / SETTINGS_FILE


def _plugins_path() -> Path:
    """The installed-plugins file; `DECKHAND_PLUGINS_FILE` moves it, which is how the tests read one."""
    override = os.environ.get("DECKHAND_PLUGINS_FILE")
    return Path(override) if override else Path.home() / PLUGINS_FILE


def superpowers_line() -> str:
    """Whether superpowers is in the plugin cache, as the one line context prints about it.

    The cache says Claude Code installed the plugin, not that the session has it enabled, and that
    is as much as this can honestly report.

    The file has held the plugin map under a `plugins` key and, in older versions, at the top level;
    the top level is read only when there is no `plugins` key at all, so a `plugins` value of the
    wrong shape is a file this cannot read rather than a map to look through.
    """
    try:
        data = json.loads(_plugins_path().read_text(encoding="utf-8"))
    except Exception as error:
        return f"superpowers: unknown ({reason(error)})"
    if not isinstance(data, dict):
        return NO_PLUGIN_LIST
    plugins = data["plugins"] if "plugins" in data else data
    if not isinstance(plugins, dict):
        return NO_PLUGIN_LIST
    if any(str(name).startswith(SUPERPOWERS) for name in plugins):
        return "superpowers: installed"
    return "superpowers: not found; install it first"


def _override_source() -> str:
    """Where the override `load` picked up came from: the environment wins over the settings file."""
    if os.environ.get("DECKHAND_PROJECT"):
        return "DECKHAND_PROJECT in the environment"
    return str(_settings_path())


def _configure_apply(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=_project_number, help="the project number to link")
    parser.add_argument("--owner", help="the project's owner (default: the repository owner)")


# --- context ------------------------------------------------------------


def context(args: argparse.Namespace) -> int:
    """Print the repository, its projects, the override, the fields, the checklist, and superpowers."""
    settings = config.load(Path.cwd())  # a malformed .claude/settings.json is worth reporting
    try:
        repo = gh.repo_slug()
    except gh.GhError as error:
        repo = None
        print(f"Repository: unknown ({error})")
    else:
        print(f"Repository: {repo}")

    linked = _print_linked(repo)
    _print_open(repo)

    if settings.override:
        print(f"Project override: {settings.override} ({_override_source()})")
    else:
        print("Project override: none (the linked project is used)")
    # Seed what resolve() would fetch again from the same list, on both paths.
    wanted = settings.override or (str(linked[0].number) if len(linked) == 1 else None)
    for project in linked:
        if str(project.number) == wanted:
            settings.resolved = project

    _print_fields(settings)
    try:
        fields, unread = gh.project_fields(settings), None
    except Exception as error:
        fields, unread = None, reason(error)
    print("Checklist:")
    for item in checklist.checklist(settings, repo, fields, unread):
        print(f"  {item.name}: {item.left or 'done'}")
    print(superpowers_line())
    return 0


def _print_linked(repo: str | None) -> list[gh.LinkedProject]:
    """Print the projects GitHub has linked to `repo`, the source every command resolves from."""
    if not repo:
        print("Linked projects: unknown (no repository)")
        return []
    try:
        linked = gh.linked_projects(repo)
    except gh.GhError as error:
        print(f"Linked projects: unknown ({error})")
        return []
    if not linked:
        print("Linked projects: none")
        return []
    print("Linked projects:")
    for project in linked:
        print(f"  {project}")
    return linked


def _print_open(repo: str | None) -> None:
    """Print the owner's open projects, so a person picking one for `--project` has the numbers."""
    if not repo:
        print("Open projects: unknown (no repository)")
        return
    owner = repo.split("/", 1)[0]
    label = f"{owner} (repository owner)"
    try:
        projects = gh.json_out("project", "list", "--owner", owner, "--format", "json")["projects"]
    except (gh.GhError, KeyError, TypeError) as error:
        print(f"Open projects for {label}: unknown ({error})")
        return
    open_projects = [p for p in projects if not p.get("closed")]
    if not open_projects:
        print(f"Open projects for {label}: none")
        return
    print(f"Open projects for {label}:")
    for project in open_projects:
        print(f"  {project.get('number')}  {project.get('title')}")


def _print_fields(settings: Settings) -> None:
    """Report each of the three fields as present, missing, or the wrong type."""
    try:
        existing = {f["name"]: f.get("type", "") for f in gh.field_list(settings)}
    except Exception as error:
        print(f"Fields: unknown ({error})")
        return
    print("Fields:")
    for name, data_type in PROJECT_FIELDS:
        actual = existing.get(name)
        if actual is None:
            print(f"  {name}: missing")
        elif actual == FIELD_TYPES[data_type]:
            print(f"  {name}: present")
        else:
            print(f"  {name}: wrong type ({actual}, expected {data_type})")


# --- apply --------------------------------------------------------------


@step("setup", _configure_apply, issue_bound=False)
def apply(args: argparse.Namespace) -> int:
    """Point this repository at its project, make squash the only merge, and fix the fields."""
    repo = gh.repo_slug()
    settings = config.load(Path.cwd())
    if args.project is not None:
        _target(settings, repo, args.project, args.owner)
    _resolve(settings, repo)
    print(f"Project: {settings.owner} #{settings.project}")
    _set_merges()
    _create_fields(settings)
    kept = _recolor_kind(settings, _delete_extra_fields(settings))
    _print_left(checklist.checklist(settings, repo, kept))
    return 0


def _print_left(items: list[Item]) -> None:
    """Print what a person still has to do, with the click for each, apart from what could not be read."""
    todo = [item for item in items if item.left and not item.unknown]
    unread = [item for item in items if item.unknown]
    if not todo and not unread:
        print("Setup complete.")
        return
    print()
    if todo:
        print("Finish by hand (the API cannot do this):")
        for index, item in enumerate(todo, start=1):
            note = checklist.NOTES.get(item.name)
            print(f"  {index}. {item.name}: {item.left}. {item.click}." + (f" {note}" if note else ""))
    if unread:
        print("Could not read:")
        for item in unread:
            print(f"  {item.name}: {item.left}")
    print("Run setup again when done; it says what is still left.")


def _target(settings: Settings, repo: str, project: str, owner: str | None) -> None:
    """Work against `project`: link it unless it is linked already, and resolve against it either way.

    A repository linked to a different project is not an error here. The number the user named is
    the one this run works on and the one a later run finds linked, so setup is a no-op the second
    time.
    """
    linked = gh.linked_projects(repo)
    settings.override = project
    for existing in linked:
        if str(existing.number) == project:
            settings.resolved = existing  # already linked: the list just read is what resolve() wants
            return
    login = owner or repo.split("/", 1)[0]
    try:
        gh.run("project", "link", project, "--owner", login, "--repo", repo)
    except gh.GhError as error:
        raise Refusal(str(error)) from error
    print(f"linked {login} #{project} to {repo}")


def _resolve(settings: Settings, repo: str) -> None:
    """Resolve the project the rest of this command writes to; without exactly one, refuse.

    The message for several linked projects already names its fix, so it is passed through as it is.
    """
    try:
        settings.resolve()
    except config.NoProject as error:
        raise Refusal(f"no project is linked to {repo}; link one with --project N") from error
    except config.ConfigError as error:
        raise Refusal(str(error)) from error


def _set_merges() -> None:
    """Make squash the only merge method, with the pull request as the message."""
    try:
        gh.run(*MERGE_SETTINGS)
    except gh.GhError as error:
        raise Refusal(str(error)) from error
    print("merge: squash only, message from the pull request")


def _create_fields(settings: Settings) -> None:
    """Create the project fields that are missing."""
    existing = {f["name"]: f.get("type", "") for f in gh.field_list(settings)}

    for name, data_type in PROJECT_FIELDS:
        if name in existing:
            expected = FIELD_TYPES[data_type]
            if existing[name] == expected:
                print(f"{name}: already present")
            else:
                print(f"{name}: present but is {existing[name]}, expected {data_type} (fix by hand)")
            continue
        if data_type == "SINGLE_SELECT":
            gh.create_single_select(settings, name, checklist.kind_options(settings.kinds))
        else:
            project, owner = str(settings.project), settings.owner
            gh.run("project", "field-create", project, "--owner", owner, "--name", name, "--data-type", data_type)
        print(f"{name}: created")


def _delete_extra_fields(settings: Settings) -> list[dict[str, Any]]:
    """Delete every field a person added that the process does not read; returns the fields kept.

    A field's data type says who made it: only the five `field-create` offers can be deleted, and
    GitHub's own columns report a type of their own, so nothing here can reach them by accident.
    """
    kept = []
    for field in gh.project_fields(settings):
        name = str(field.get("name") or "")
        if field.get("dataType") not in DELETABLE_TYPES or name in KEPT_FIELDS:
            kept.append(field)
            continue
        gh.run("project", "field-delete", "--id", str(field.get("id")))
        print(f"deleted field {name}")
    return kept


def _recolor_kind(settings: Settings, kept: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rewrite Kind's options when a configured kind is missing or the wrong color.

    Returns `kept` with the options as written, so the checklist reads what the board has now
    without a second query.
    """
    kind = next((f for f in kept if f["name"] == "Kind" and f.get("dataType") == "SINGLE_SELECT"), None)
    options = checklist.kind_update(kind, settings.kinds) if kind else None
    if kind is None or options is None:
        return kept
    gh.update_single_select(settings, str(kind["id"]), options)
    print("Kind: recolored")
    return [{**field, "options": options} if field is kind else field for field in kept]
