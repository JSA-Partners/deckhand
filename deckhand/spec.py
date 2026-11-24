from __future__ import annotations

from dataclasses import dataclass

from deckhand import planning
from deckhand.features import Feature
from deckhand.records import Record
from deckhand.store import Store

MISSING = "not written"


@dataclass(frozen=True)
class Spec:
    contract: str | None
    examples: str | None
    invariants: str | None
    scope: str
    decisions: list[dict]


def _answered(feature: Feature) -> list[dict]:
    latest: dict[str, Record] = {}
    for story in feature.stories:
        for record in feature.records.get(story["id"], []):
            if record.kind == "decision-item":
                latest[record.subject] = record
    return [
        {"item": str(r.data.get("item", r.subject)), "answer": r.data["answer"], "note": r.data.get("note")}
        for r in latest.values()
        if r.data.get("answer")
    ]


def gather(store: Store, feature: Feature, story: dict) -> Spec:
    description = story["description"] or ""
    return Spec(
        planning.section(store, feature, "Contract"),
        planning.section(store, feature, "Examples"),
        planning.section(store, feature, "Invariants"),
        f"{story['title']}\n\n{description}" if description else story["title"],
        _answered(feature),
    )


def _decision(entry: dict) -> str:
    note = f" ({entry['note']})" if entry["note"] else ""
    return f"- {entry['item']}: {entry['answer']}{note}"


def text(spec: Spec) -> str:
    decisions = "\n".join(_decision(entry) for entry in spec.decisions)
    parts = [
        ("Contract", spec.contract),
        ("Examples", spec.examples),
        ("Invariants", spec.invariants),
        ("Scope", spec.scope),
        ("Decisions", decisions),
    ]
    return "\n\n".join(f"# {name}\n\n{body or MISSING}" for name, body in parts) + "\n"


def pointer(ref: str) -> str:
    return (
        f"You are the hand of {ref}. Before anything else run: deckhand build context {ref}. It prints what"
        " the person approved (Contract, Examples, Invariants, scope, decisions) and your plan."
    )
