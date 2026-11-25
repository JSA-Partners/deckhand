from __future__ import annotations

from typing import TYPE_CHECKING

from deckhand import ops
from deckhand.step import Refusal
from deckhand.store import Store

if TYPE_CHECKING:
    from deckhand.features import Feature

SECTIONS = ("Frame", "Shape", "Examples", "Pre-mortem", "Facts", "Contract", "Invariants")
STORY_MAP = "story map"
HINT_PER_WEEK = 8
WRITTEN = "Written by the person."
DRAFTED = "Drafted by an agent, accepted by the person."


def _id(feature: Feature, name: str) -> tuple[str, str]:
    return feature.project["id"], "section:" + name


def save_section(store: Store, feature: Feature, name: str, body: str, drafted: bool) -> None:
    if name not in SECTIONS:
        raise Refusal(f"{name!r} is not a planning section; the sections are {', '.join(SECTIONS)}")
    if not body.strip():
        raise Refusal(f"the {name} section is empty")
    text = f"{DRAFTED if drafted else WRITTEN}\n\n{body.strip()}"
    key, purpose = _id(feature, name)
    ops.ensure_document(store, key, purpose, key, name, text)


def document(store: Store, feature: Feature, name: str) -> dict | None:
    id = ops.create_id(*_id(feature, name))
    return next((d for d in store.project_documents(feature.project["id"]) if d["id"] == id), None)


def section(store: Store, feature: Feature, name: str) -> str | None:
    found = document(store, feature, name)
    return found["body"] if found else None


def saved_at(store: Store, feature: Feature, name: str) -> str | None:
    found = document(store, feature, name)
    return found["at"] if found else None


def next_section(store: Store, feature: Feature) -> str | None:
    for name in SECTIONS:
        if section(store, feature, name) is None:
            return name
    return None if feature.stories else STORY_MAP
