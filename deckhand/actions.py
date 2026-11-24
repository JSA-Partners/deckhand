from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from deckhand.records import Record


@dataclass(frozen=True)
class Launch:
    name: str
    ref: str
    kind: str
    clone: Path
    story: dict | None
    me: str
    restart: bool = False


@dataclass(frozen=True)
class Resume:
    name: str
    session: str
    cwd: str
    text: str
    seqs: tuple[int, ...]
    record: Record | None = None
    restart: bool = False


@dataclass(frozen=True)
class Post:
    name: str
    key: str
    kind: str
    body: str
    issue: str
    record: Record | None
    restart: bool = False


@dataclass(frozen=True)
class Stop:
    name: str
    short: str
    why: str
    seqs: tuple[int, ...] = ()


@dataclass(frozen=True)
class Remove:
    name: str
    short: str
    stop: bool = True


@dataclass(frozen=True)
class Adopt:
    name: str
    short: str
    session: str


Action = Launch | Resume | Post | Stop | Remove | Adopt
