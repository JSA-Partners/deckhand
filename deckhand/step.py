from __future__ import annotations

from collections.abc import Callable

VERBS = ("context", "apply")

REGISTRY: dict[str, object] = {}


class Refusal(Exception):
    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule


def register(name: str) -> Callable[[type], type]:
    def add(cls: type) -> type:
        REGISTRY[name] = cls()
        return cls

    return add
