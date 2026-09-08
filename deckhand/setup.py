"""Setup: report what a repository has, then link its project, set its merges, and make its fields.

Nothing here writes into a repository's files. The project comes from the GitHub project link, the
merge settings come from `gh repo edit`, the board's three fields come from the API, and everything
the API cannot do is printed as a checklist for a person to finish by hand.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from deckhand import config, gh
from deckhand.config import SETTINGS_FILE, Settings
from deckhand.step import Refusal, step

PROJECT_FIELDS: list[tuple[str, str]] = [
    ("Kind", "SINGLE_SELECT"),
    ("Story Points", "NUMBER"),
    ("Actual", "NUMBER"),
]

# What `gh project field-list` reports as `type` for each `field-create --data-type`.
FIELD_TYPES = {"SINGLE_SELECT": "ProjectV2SingleSelectField", "NUMBER": "ProjectV2Field"}

STATUS_OPTIONS = "Backlog, Blocked, In Progress, Pending Review, Done"

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
    """Print the repository, its linked and open projects, any override, and the three fields."""
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
    """Point this repository at its project, make squash the only merge, and create the fields."""
    repo = gh.repo_slug()
    settings = config.load(Path.cwd())
    if args.project is not None:
        _target(settings, repo, args.project, args.owner)
    _resolve(settings, repo)
    _set_merges()
    _create_fields(settings)
    return 0


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
    """Create the missing project fields, then print the checklist for what the API cannot do."""
    fields = gh.field_list(settings)
    existing = {f["name"]: f.get("type", "") for f in fields}

    for name, data_type in PROJECT_FIELDS:
        if name in existing:
            expected = FIELD_TYPES[data_type]
            if existing[name] == expected:
                print(f"{name}: already present")
            else:
                print(f"{name}: present but is {existing[name]}, expected {data_type} (fix by hand)")
            continue
        cmd = [
            "project",
            "field-create",
            str(settings.project),
            "--owner",
            settings.owner,
            "--name",
            name,
            "--data-type",
            data_type,
        ]
        if data_type == "SINGLE_SELECT":
            cmd += ["--single-select-options", ",".join(settings.kinds)]
        gh.run(*cmd)
        print(f"{name}: created")

    status = next((f for f in fields if f["name"] == "Status"), None)
    current = ", ".join(o["name"] for o in (status.get("options") or [])) if status else ""
    current = current or "unknown"

    print()
    print("Finish the project setup by hand (the API cannot do this):")
    print(f"  1. Rename or reorder the Status options to: {STATUS_OPTIONS}.")
    print(f"     (currently: {current})")
    print("  2. Hide the Milestone column.")
