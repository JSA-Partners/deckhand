from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from deckhand import answers, config, launch
from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.linear.workspace import AUTOMATION, STATES, Workspace
from deckhand.step import Refusal, register

REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CAPTAIN_SETTINGS = "captain-settings.json"
USAGE = (
    "setup takes --team-key KEY, --repository LABEL=owner/name (repeatable), --clone LABEL=PATH (repeatable), "
    "--slot-cap N, and --json on context only"
)

NEEDS = (
    "setup needs --team-key KEY, the key of the Linear team, or team_key in config.toml;"
    " then --repository LABEL=owner/name and --clone LABEL=PATH for each repository, and --slot-cap N"
)


def _clone(value: str) -> tuple[str, Path]:
    label, _, raw = value.partition("=")
    path = Path(raw).expanduser().absolute()
    if not label or not raw or not path.is_dir():
        raise Refusal(f"--clone LABEL=PATH needs a directory, not {value!r}")
    return label, path


def _slot_cap(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise Refusal(f"--slot-cap takes a positive integer, not {value!r}")
    return int(value)


def _parse(args: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {"team_key": "", "repositories": {}, "clones": {}, "slot_cap": None}
    rest = iter(args)
    for arg in rest:
        if arg not in ("--team-key", "--repository", "--clone", "--slot-cap"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        value = next(rest, "")
        if arg == "--team-key":
            parsed["team_key"] = value
        elif arg == "--repository":
            label, _, repository = value.partition("=")
            if not label or not REPOSITORY.match(repository):
                raise Refusal(f"--repository takes LABEL=owner/name, not {value!r}")
            parsed["repositories"][label] = repository
        elif arg == "--clone":
            label, path = _clone(value)
            parsed["clones"][label] = path
        else:
            parsed["slot_cap"] = _slot_cap(value)
    if not parsed["team_key"]:
        raise Refusal(f"--team-key is required; {USAGE}")
    return parsed


def _config_text(team_key: str, slot_cap: int, sandbox_write: list[str], clones: dict[str, Path]) -> str:
    lines = [f"team_key = {json.dumps(team_key)}", f"slot_cap = {slot_cap}"]
    if sandbox_write:
        lines.append(f"sandbox_write = {json.dumps(sandbox_write)}")
    if clones:
        lines += ["", "[clones]"]
        lines += [f"{json.dumps(label)} = {json.dumps(str(path))}" for label, path in sorted(clones.items())]
    return "\n".join(lines) + "\n"


def _local_files(parsed: dict[str, Any]) -> dict[Path, str]:
    directory = config.config_dir()
    current = config.local()
    text = _config_text(
        parsed["team_key"],
        parsed["slot_cap"] or current.slot_cap,
        current.sandbox_write,
        {**current.clones, **parsed["clones"]},
    )
    captain = launch.captain_settings(launch.protected(directory, answers.state_dir()))
    return {directory / config.FILE: text, directory / CAPTAIN_SETTINGS: json.dumps(captain, indent=2) + "\n"}


def _differs(path: Path, text: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") != text
    except FileNotFoundError:
        return True


def _configured_key() -> str:
    try:
        return config.local().team_key
    except Refusal as error:
        print(error)
        return ""


def _launch_line() -> str:
    settings = shlex.quote(str(config.config_dir() / CAPTAIN_SETTINGS))
    return f'claude --settings {settings} --name captain "/deckhand:captain"'


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
        args = [arg for arg in args if arg != "--json"]
        if "--team-key" not in args:
            key = _configured_key()
            if not key:
                print(NEEDS)
                return
            args = ["--team-key", key, *args]
        parsed = _parse(args)
        key = parsed["team_key"]
        facts = self._facts(key, parsed["repositories"])
        if facts["team"]["found"]:
            local = _local_files(parsed)
            facts["local_changes"] = [str(path) for path, text in local.items() if _differs(path, text)]
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
        for path in facts["local_changes"]:
            print(f"would write {path}")
        print(f"then launch: {_launch_line()}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        key = parsed["team_key"]
        files = _local_files(parsed)
        workspace = self._workspace_for_run()
        team = workspace.team(key)
        if team is None:
            raise Refusal(f"no Linear team has the key {key}")
        changes = workspace.plan(team, list(parsed["repositories"]))
        workspace.apply(team, changes)
        for change in changes:
            print(f"done: {change.text}")
        if not changes:
            print("no changes")
        for path, text in files.items():
            if _differs(path, text):
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                print(f"wrote {path}")
        print(_launch_line())
        sys.stdout.flush()
