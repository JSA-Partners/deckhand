from __future__ import annotations

from deckhand import order, queue, spec, versions
from deckhand.features import Feature
from deckhand.records import Record
from deckhand.store import Store

SECTIONS = ("Contract", "Examples", "Invariants")


def item_answer(records: list[Record], item: str, draft: str) -> str:
    answered = versions.answer(records, item, draft)
    return answered if answered is not None else f"{draft} (not decided)"


def _listed(name: str, lines: list[str]) -> str:
    return f"{name}:\n" + "".join(f"{line}\n" for line in lines) if lines else f"{name}: none\n"


def _story(store: Store, feature: Feature, story: dict) -> str:
    records = feature.records.get(story["id"], [])
    ids = versions.blockers(store, feature, story)
    blockers = [s["identifier"] for s in feature.live if s["id"] in ids]
    repositories = versions.repositories(store, story)
    level = versions.tier(records, story["id"])
    items = [f"- {item}: {item_answer(records, item, draft)}" for item, draft in versions.drafts(records).items()]
    chart = versions.chart(records, story["id"]).strip()
    head = [
        f"## {story['identifier']} {story['title']}",
        f"repository: {', '.join(repositories) or 'none'}",
        f"depends on: {', '.join(blockers) or 'none'}",
        f"tier: {level.label if level else 'not given'}",
        "",
        (story["description"] or "").strip() or "no description",
        "",
    ]
    kinds = {r.kind for r in records}
    tail = "draft: saved, not taken\n" if queue.DRAFT in kinds and queue.TAKEN not in kinds else ""
    tail += _listed("decisions", items)
    tail += f"state chart:\n{chart}\n" if chart else "state chart: none\n"
    tail += _listed("after merge", [f"- {item}" for item in versions.after_merge(records)])
    return "\n".join(head) + "\n" + tail


def screen(store: Store, feature: Feature) -> str:
    edges = order.edges(store, feature)
    stories, _ = order.build_order(feature.live, edges)
    shown = [spec.planning_text(store, feature, SECTIONS), "# Story map\n"]
    shown += [_story(store, feature, s) for s in stories]
    return "\n".join(spec.plain(part) for part in shown)
