from __future__ import annotations

import re
from dataclasses import dataclass

_REF = re.compile(r"([A-Za-z][A-Za-z0-9]*)-([1-9][0-9]*)")


@dataclass(frozen=True)
class Ref:
    team: str
    number: int

    @classmethod
    def parse(cls, text: str) -> Ref:
        match = _REF.fullmatch(text)
        if match is None:
            raise ValueError(f"not a story reference: {text!r}")
        return cls(match.group(1).upper(), int(match.group(2)))

    def __str__(self) -> str:
        return f"{self.team}-{self.number}"
