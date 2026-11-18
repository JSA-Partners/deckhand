from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from deckhand.step import Refusal, read_toml

KEYS = frozenset({"clones", "slot_cap"})


@dataclass(frozen=True)
class LocalConfig:
    clones: dict[str, Path] = field(default_factory=dict)
    slot_cap: int = 1

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
        return cls(
            clones={label: Path(clone).expanduser() for label, clone in clones.items()},
            slot_cap=slot_cap,
        )
