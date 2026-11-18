from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from deckhand.credentials import Credentials
from deckhand.github import Github
from deckhand.linear.connect import connect
from deckhand.linear.workspace import AUTOMATION, STATES, Workspace
from deckhand.step import Refusal, register

REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
USAGE = "setup takes --team-key KEY, --repository LABEL=owner/name (repeatable), --enable-auto-merge"


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


def _parse(args: list[str]) -> tuple[str, dict[str, str], bool]:
    key = ""
    repositories: dict[str, str] = {}
    enable = False
    rest = iter(args)
    for arg in rest:
        if arg == "--enable-auto-merge":
            enable = True
        elif arg in ("--team-key", "--repository"):
            value = next(rest, "")
            if arg == "--team-key":
                key = value
            else:
                label, _, repository = value.partition("=")
                if not label or not REPOSITORY.match(repository):
                    raise Refusal(f"--repository takes LABEL=owner/name, not {value!r}")
                repositories[label] = repository
        else:
            raise Refusal(f"unknown argument {arg}; {USAGE}")
    if not key:
        raise Refusal(f"--team-key is required; {USAGE}")
    return key, repositories, enable


@register("setup")
class Setup:
    def __init__(self, workspace: Workspace | None = None, github: Github | None = None) -> None:
        self._workspace = workspace
        self._github = github or Github()

    def _workspace_for_run(self) -> Workspace:
        if self._workspace is not None:
            return self._workspace
        return Workspace(connect(Credentials.load(_config_dir()), "person"))

    def context(self, args: list[str]) -> None:
        key, repositories, _ = _parse(args)
        workspace = self._workspace_for_run()
        team = workspace.team(key)
        if team is None:
            print(f"team {key}: no team has this key; apply refuses")
            return
        print(f"team {key}: found")
        designed = set(STATES)
        for state in team["states"]["nodes"]:
            note = "" if state["name"] in designed else " (outside the design, kept as is)"
            print(f"state {state['name']} ({state['type']}){note}")
        for event, row in ((r["event"], r) for r in team["gitAutomationStates"]["nodes"]):
            print(f"automation {event} to {(row['state'] or {}).get('name')}")
        for label in team["labels"]["nodes"]:
            print(f"label {label['name']}")
        for label, repository in repositories.items():
            allowed = self._github.auto_merge_allowed(repository)
            print(f"repository {repository} (label {label}): allow_auto_merge {allowed}")
        print(f"design automation: {', '.join(f'{e} to {s}' for e, s in AUTOMATION.items())}")
        changes = workspace.plan(team, list(repositories))
        print("planned changes:" if changes else "planned changes: none")
        for change in changes:
            print(f"  {change.text}")
        print("  allow_auto_merge on each repository only with --enable-auto-merge")

    def apply(self, args: list[str]) -> None:
        key, repositories, enable = _parse(args)
        workspace = self._workspace_for_run()
        team = workspace.team(key)
        if team is None:
            raise Refusal(f"no Linear team has the key {key}")
        changes = workspace.plan(team, list(repositories))
        pending = []
        if enable:
            pending = [r for r in repositories.values() if not self._github.auto_merge_allowed(r)]
        workspace.apply(team, changes)
        for change in changes:
            print(f"done: {change.text}")
        for repository in pending:
            self._github.allow_auto_merge(repository)
            print(f"done: enable allow_auto_merge on {repository}")
        if not changes and not pending:
            print("no changes")
        sys.stdout.flush()
