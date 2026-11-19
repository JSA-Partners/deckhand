from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

VERBS = ("context", "apply")

REGISTRY: dict[str, object] = {}


class Refusal(Exception):
    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule


def register(name: str) -> Callable[[type], type]:
    def add(cls: type) -> type:
        if name in REGISTRY:
            raise ValueError(f"command registered twice: {name}")
        REGISTRY[name] = cls()
        return cls

    return add


def read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        raise Refusal(f"{path}: file not found") from None
    except tomllib.TOMLDecodeError:
        raise Refusal(f"{path}: not valid TOML") from None


def team_key(parsed: dict) -> str:
    from deckhand import config

    key = parsed.get("team_key") or config.local().team_key
    if not key:
        raise Refusal(f"no team key: pass --team-key or set team_key in {config.config_dir() / config.FILE}")
    return key
