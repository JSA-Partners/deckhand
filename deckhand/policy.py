from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deckhand.step import Refusal, read_toml

STAGES = ("plan", "branch")
TIER_KEYS = ("0", "1", "2", "3", "4")
KEYS = frozenset(
    {"repository_label", "tier_tools", "checks", "sensitive_paths", "extra_lenses", "front_end", "second_signoff"}
)


@dataclass(frozen=True)
class Policy:
    repository_label: str
    tier_tools: dict[int, list[str]] = field(default_factory=dict)
    checks: list[str] = field(default_factory=list)
    sensitive_paths: list[str] = field(default_factory=list)
    extra_lenses: dict[str, list[str]] = field(default_factory=dict)
    front_end: bool = False
    second_signoff: bool = False

    @classmethod
    def load(cls, repo_root: Path) -> Policy:
        path = repo_root / ".claude" / "deckhand.toml"
        table = read_toml(path)
        for key in table:
            if key not in KEYS:
                raise Refusal(f"{path}: unknown key {key}")
        label = table.get("repository_label")
        if not isinstance(label, str) or not label:
            raise Refusal(f"{path}: repository_label must be a non-empty string")
        front_end = _flag(path, table, "front_end")
        return cls(
            repository_label=label,
            tier_tools=_tier_tools(path, table.get("tier_tools", {})),
            checks=_strings(path, "checks", table.get("checks", [])),
            sensitive_paths=_strings(path, "sensitive_paths", table.get("sensitive_paths", [])),
            extra_lenses=_extra_lenses(path, table.get("extra_lenses", {})),
            front_end=front_end,
            second_signoff=_flag(path, table, "second_signoff"),
        )


def _flag(path: Path, table: dict, name: str) -> bool:
    value = table.get(name, False)
    if not isinstance(value, bool):
        raise Refusal(f"{path}: {name} must be true or false")
    return value


def _strings(path: Path, name: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise Refusal(f"{path}: {name} must be a list of strings")
    return value


def _tier_tools(path: Path, value: Any) -> dict[int, list[str]]:
    if not isinstance(value, dict):
        raise Refusal(f"{path}: tier_tools must be a table")
    tools = {}
    for key, names in value.items():
        if key not in TIER_KEYS:
            raise Refusal(f"{path}: tier_tools key {key!r} is not a tier from 0 to 4")
        tools[int(key)] = _strings(path, f"tier_tools.{key}", names)
    return tools


def _extra_lenses(path: Path, value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise Refusal(f"{path}: extra_lenses must be a table")
    found = {}
    for stage, lenses in value.items():
        if stage not in STAGES:
            raise Refusal(f"{path}: extra_lenses key {stage!r} is not a stage, plan or branch")
        names = _strings(path, f"extra_lenses.{stage}", lenses)
        for name in names:
            if not name or "/" in name or name.endswith(".md"):
                raise Refusal(f"{path}: extra_lenses.{stage} entry {name!r} must be a lens name, not a path")
        found[stage] = names
    return found
