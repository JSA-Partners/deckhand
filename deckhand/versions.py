from __future__ import annotations

import json
from typing import TYPE_CHECKING

from deckhand import planning, tiers
from deckhand.records import Record, content_hash
from deckhand.step import Refusal
from deckhand.store import Store

if TYPE_CHECKING:
    from deckhand.features import Feature

STORY_KINDS = ("lean",)
MAP_AUTHOR = "agent:stories"


def _hash(*parts: str) -> str:
    return content_hash("\n".join(content_hash(part) for part in parts))


def item_subject(text: str) -> str:
    return content_hash(text)[:12]


def held(records: list[Record], kind: str, subject: str) -> Record | None:
    found = [r for r in records if r.kind == kind and r.subject == subject]
    return found[-1] if found else None


def mapped(records: list[Record], kind: str, story: str) -> Record | None:
    found = held(records, kind, story)
    return found if found is not None and found.author == MAP_AUTHOR else None


def tier(records: list[Record], story: str) -> tiers.Tier | None:
    found = mapped(records, "tier", story)
    return tiers.from_record(found.data) if found else None


def chart(records: list[Record], story: str) -> str:
    found = mapped(records, "state-chart", story)
    text = found.data.get("chart") if found else None
    return text if isinstance(text, str) else ""


def story_text_version(issue: dict) -> str:
    return _hash(issue["title"], issue["description"] or "")


def story_version(issue: dict, records: list[Record]) -> str:
    found = mapped(records, "tier", issue["id"])
    fact = str(found.data.get("fact", "")) if found else ""
    return _hash(story_text_version(issue), found.version if found else "", fact)


def chart_version(chart: str) -> str:
    return content_hash(chart)


def _stub(records: list[Record]) -> dict:
    stub = next((r for r in records if r.kind == "stub"), None)
    return stub.data if stub else {}


def expected_items(records: list[Record]) -> object:
    return _stub(records).get("decisions", [])


def item_version(item: str, draft: str) -> str:
    return content_hash(item + "\0" + draft)


def drafts(records: list[Record]) -> dict[str, str]:
    expected = expected_items(records)
    entries = [e for e in expected if isinstance(e, dict)] if isinstance(expected, list) else []
    return {str(e.get("item", "")): str(e.get("draft", "")) for e in entries}


def after_merge(records: list[Record]) -> list[str]:
    items = _stub(records).get("after_merge", [])
    return [item for item in items if isinstance(item, str)] if isinstance(items, list) else []


def feature_version(store: Store, feature: Feature) -> str:
    parts = [planning.section(store, feature, name) or "" for name in planning.SECTIONS]
    for story in sorted(feature.live, key=lambda s: s["id"]):
        records = feature.records.get(story["id"], [])
        chart = mapped(records, "state-chart", story["id"])
        items = json.dumps([expected_items(records), after_merge(records)], sort_keys=True)
        parts += [story["id"], story_version(story, records), items, chart.version if chart else ""]
    return _hash(*parts)


def story_of(feature: Feature, id: str) -> dict:
    found = next((s for s in feature.live if s["id"] == id), None)
    if found is None:
        raise Refusal(f"{id} is not a story of {feature.project['name']}")
    return found


def version_for(store: Store, feature: Feature, kind: str, subject: str) -> str:
    if kind == "feature-approval":
        return feature_version(store, feature)
    if kind not in STORY_KINDS:
        raise Refusal(f"no version is defined for the decision kind {kind!r}")
    story = story_of(feature, subject)
    return story_version(story, feature.records.get(story["id"], []))


def current(store: Store, feature: Feature, record: Record) -> bool:
    return record.version == version_for(store, feature, record.kind, record.subject)
