from __future__ import annotations

import json
import re
import sys
from typing import Any

from deckhand import config
from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.linear.workspace import AUTOMATION, STATES, Workspace
from deckhand.step import Refusal, register

REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
USAGE = "setup takes --team-key KEY, --repository LABEL=owner/name (repeatable), and --json on context only"


def _parse(args: list[str]) -> tuple[str, dict[str, str]]:
    key = ""
    repositories: dict[str, str] = {}
    rest = iter(args)
    for arg in rest:
        if arg in ("--team-key", "--repository"):
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
    return key, repositories


@register("setup")
class Setup:
    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    def _workspace_for_run(self) -> Workspace:
        if self._workspace is not None:
            return self._workspace
        return Workspace(connect(Credentials.load(config.config_dir()), "person"))

    def _facts(self, key: str, repositories: dict[str, str]) -> dict[str, Any]:
        workspace = self._workspace_for_run()
        team = workspace.team(key)
        if team is None:
            return {"team": {"key": key, "found": False}}
        return {
            "team": {"key": key, "found": True},
            "states": [
                {"name": s["name"], "type": s["type"], "designed": s["name"] in STATES} for s in team["states"]["nodes"]
            ],
            "automation": [
                {"event": row["event"], "state": (row["state"] or {}).get("name")}
                for row in team["gitAutomationStates"]["nodes"]
            ],
            "labels": [label["name"] for label in team["labels"]["nodes"]],
            "repositories": [{"label": label, "repository": repository} for label, repository in repositories.items()],
            "planned_changes": [change.text for change in workspace.plan(team, list(repositories))],
        }

    def context(self, args: list[str]) -> None:
        as_json = "--json" in args
        key, repositories = _parse([arg for arg in args if arg != "--json"])
        facts = self._facts(key, repositories)
        if as_json:
            print(json.dumps(facts, indent=2))
            return
        if not facts["team"]["found"]:
            print(f"team {key}: no team has this key; apply refuses")
            return
        print(f"team {key}: found")
        for state in facts["states"]:
            note = "" if state["designed"] else " (outside the design, kept as is)"
            print(f"state {state['name']} ({state['type']}){note}")
        for row in facts["automation"]:
            print(f"automation {row['event']} to {row['state']}")
        for label in facts["labels"]:
            print(f"label {label}")
        for row in facts["repositories"]:
            print(f"repository {row['repository']} (label {row['label']})")
        print(f"design automation: {', '.join(f'{e} to {s}' for e, s in AUTOMATION.items())}")
        changes = facts["planned_changes"]
        print("planned changes:" if changes else "planned changes: none")
        for change in changes:
            print(f"  {change}")

    def apply(self, args: list[str]) -> None:
        key, repositories = _parse(args)
        workspace = self._workspace_for_run()
        team = workspace.team(key)
        if team is None:
            raise Refusal(f"no Linear team has the key {key}")
        changes = workspace.plan(team, list(repositories))
        workspace.apply(team, changes)
        for change in changes:
            print(f"done: {change.text}")
        if not changes:
            print("no changes")
        sys.stdout.flush()
