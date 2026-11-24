from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from deckhand import launch, planning
from deckhand.features import Feature
from deckhand.records import Record
from deckhand.store import Store

MISSING = "not written"
DRAFT_SECTIONS = ("Frame", "Shape", "Contract", "Examples")
# Claude Code cuts a skill's injected output over about 25 KB to a short preview.
LIMIT = 20_000
CUT = "(cut here to fit; the rest is in the file below)\n"


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


def _blocks(parts: list[tuple[str, str | None]]) -> str:
    return "\n\n".join(f"# {name}\n\n{body or MISSING}" for name, body in parts) + "\n"


def text(spec: Spec) -> str:
    decisions = "\n".join(_decision(entry) for entry in spec.decisions)
    return _blocks(
        [
            ("Contract", spec.contract),
            ("Examples", spec.examples),
            ("Invariants", spec.invariants),
            ("Scope", spec.scope),
            ("Decisions", decisions),
        ]
    )


def planning_text(store: Store, feature: Feature, names: tuple[str, ...]) -> str:
    return _blocks([(name, planning.section(store, feature, name)) for name in names])


def pointer(ref: str) -> str:
    return (
        f"You are the hand of {ref}. What the person approved (Contract, Examples, Invariants, scope, decisions)"
        f" and your plan are in deckhand build context {ref}; run it unless it is already above."
    )


def draft_pointer(ref: str) -> str:
    return (
        f"You are the draft hand of {ref}. The story's stub, the feature's Frame, Shape, Contract and Examples,"
        f" and what the draft holds are in deckhand draft context {ref}; run it unless it is already above."
    )


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


# The folder is one a hand can write in, so a planted link is removed rather than followed.
def _save(name: str, text: str) -> Path | None:
    try:
        path = launch.input_folder() / f"{name}.md"
        path.unlink(missing_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        return None
    return path


def _cut(text: str, room: int) -> str:
    kept: list[str] = []
    used = _size(CUT)
    for line in text.splitlines(keepends=True):
        used += _size(line)
        if used >= room:
            break
        kept.append(line)
    return "".join(kept) + CUT


def fitted(name: str, first: str, rest: str) -> str:
    whole = first + rest
    if _size(whole) < LIMIT:
        return whole
    path = _save(name, whole)
    if path is None:
        where = "the rest of this context did not fit here and could not be saved to a file\n"
    else:
        where = f"the whole context, the rest included, is in {path}; read it before you go on\n"
    room = LIMIT - _size(where)
    return (first if _size(first) < room else _cut(first, room)) + where
