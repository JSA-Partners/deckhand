from __future__ import annotations

from deckhand import versions
from deckhand.records import Record

KIND = "checks"


def passing(records: list[Record], story: str, head: str) -> bool:
    held = versions.held(records, KIND, story)
    return held is not None and held.version == head and bool(held.data.get("passed"))
