from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from deckhand.step import Refusal, read_toml

FILE = "config.toml"
KEYS = frozenset({"clones", "slot_cap", "team_key", "sandbox_write", "teams"})


def config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


def local() -> LocalConfig:
    return LocalConfig.load(config_dir() / FILE)


@dataclass(frozen=True)
class LocalConfig:
    clones: dict[str, Path] = field(default_factory=dict)
    slot_cap: int = 1
    team_key: str = ""
    sandbox_write: list[str] = field(default_factory=list)
    teams: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> LocalConfig:
        if not path.exists():
            return cls()
        table = read_toml(path)
        for key in table:
            if key not in KEYS:
                raise Refusal(f"{path}: unknown key {key}")
        clones = table.get("clones", {})
        if not isinstance(clones, dict):
            raise Refusal(f"{path}: clones must be a table")
        for label, clone in clones.items():
            if not isinstance(clone, str):
                raise Refusal(f"{path}: clones.{label} must be a path string")
        slot_cap = table.get("slot_cap", 1)
        if isinstance(slot_cap, bool) or not isinstance(slot_cap, int) or slot_cap < 1:
            raise Refusal(f"{path}: slot_cap must be a positive integer")
        team_key = table.get("team_key", "")
        if not isinstance(team_key, str):
            raise Refusal(f"{path}: team_key must be a string")
        sandbox_write = table.get("sandbox_write", [])
        if not isinstance(sandbox_write, list) or not all(isinstance(item, str) for item in sandbox_write):
            raise Refusal(f"{path}: sandbox_write must be a list of strings")
        teams = table.get("teams", {})
        if not isinstance(teams, dict):
            raise Refusal(f"{path}: teams must be a table")
        for key, id in teams.items():
            if not isinstance(id, str):
                raise Refusal(f"{path}: teams.{key} must be a team id string")
        return cls(
            clones={label: Path(clone).expanduser() for label, clone in clones.items()},
            slot_cap=slot_cap,
            team_key=team_key,
            sandbox_write=sandbox_write,
            teams=teams,
        )
