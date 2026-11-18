from __future__ import annotations

import json
from typing import TYPE_CHECKING

from deckhand import planning
from deckhand.records import Record, content_hash
from deckhand.step import Refusal
from deckhand.store import Store

if TYPE_CHECKING:
    from deckhand.features import Feature

SECTION_OF = {"contract": "Contract", "invariants": "Invariants"}
STORY_KINDS = ("tier", "sitting", "state-chart")


def _hash(*parts: str) -> str:
    return content_hash("\n".join(content_hash(part) for part in parts))


def item_subject(text: str) -> str:
    return content_hash(text)[:12]


def held(records: list[Record], kind: str, subject: str) -> Record | None:
    found = [r for r in records if r.kind == kind and r.subject == subject]
    return found[-1] if found else None


def story_text_version(issue: dict) -> str:
    return _hash(issue["title"], issue["description"] or "")


def story_version(issue: dict, records: list[Record]) -> str:
    tier = held(records, "tier", issue["id"])
    answer = str(tier.data.get("answer", "")) if tier else ""
    return _hash(story_text_version(issue), tier.version if tier else "", answer)


def expected_items(records: list[Record]) -> object:
    stub = next((r for r in records if r.kind == "stub"), None)
    return stub.data.get("decisions", []) if stub else []


def feature_version(store: Store, feature: Feature) -> str:
    parts = [planning.section(store, feature, name) or "" for name in planning.SECTIONS]
    for story in sorted(feature.live, key=lambda s: s["id"]):
        records = feature.records.get(story["id"], [])
        chart = held(records, "state-chart", story["id"])
        items = json.dumps(expected_items(records), sort_keys=True)
        parts += [story["id"], story_version(story, records), items, chart.version if chart else ""]
    return _hash(*parts)


def story_of(feature: Feature, id: str) -> dict:
    found = next((s for s in feature.live if s["id"] == id), None)
    if found is None:
        raise Refusal(f"{id} is not a story of {feature.project['name']}")
    return found


def version_for(store: Store, feature: Feature, kind: str, subject: str) -> str:
    if kind in SECTION_OF:
        return _hash(planning.section(store, feature, SECTION_OF[kind]) or "")
    if kind == "feature-approval":
        return feature_version(store, feature)
    if kind == "decision-item":
        return content_hash(subject)
    if kind not in STORY_KINDS:
        raise Refusal(f"no version is defined for the decision kind {kind!r}")
    story = story_of(feature, subject)
    if kind == "tier":
        return story_text_version(story)
    return story_version(story, feature.records.get(story["id"], []))


def current(store: Store, feature: Feature, record: Record) -> bool:
    if record.kind == "decision-item":
        return True
    return record.version == version_for(store, feature, record.kind, record.subject)
