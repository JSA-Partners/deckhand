from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

VERBS = ("context", "apply")

REGISTRY: dict[str, object] = {}


def _any(rest: list[str]) -> bool:
    return True


HAND_APPLIES: dict[str, Callable[[list[str]], bool]] = {
    "build": _any,
    "implplan": _any,
    "review": _any,
    "checks": _any,
    "quiz": _any,
    "signoff": lambda rest: rest[:1] == ["summary"],
    "merge": _any,
    "ask": _any,
    "after": _any,
    "spike": _any,
    "draft": lambda rest: "take" not in rest,
    "hook": lambda rest: rest[:1] in (["session"], ["permission"]),
}
UNREFERENCED = ("hook",)


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


def hand_role() -> bool:
    return os.environ.get("DECKHAND_ROLE") == "hand"


def hand_may_apply(command: str, args: list[str]) -> bool:
    allowed = HAND_APPLIES.get(command)
    if allowed is None:
        return False
    if command in UNREFERENCED:
        return allowed(args)
    own = os.environ.get("DECKHAND_SPIKE" if command == "spike" else "DECKHAND_STORY", "")
    return bool(own) and args[:1] == [own] and allowed(args[1:])
