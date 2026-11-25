from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from deckhand import launch, planning, versions
from deckhand.features import Feature
from deckhand.records import Record
from deckhand.store import Store

MISSING = "not written"
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
    after_merge: list[str]


_CODE = re.compile(
    r"(^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}\2[`~]*[ \t]*$|\Z)|(?<!\\)(`+)(?!`)(?:(?!\n[ \t]*\n).)+?(?<!`)\3(?!`))",
    re.MULTILINE | re.DOTALL,
)
_AUTOLINK = re.compile(r"\[([^\]\n]+)\]\(<https?://([^>\s]+)>\)")
_ESCAPED = re.compile(r"\\([*_`])")


def _unmarked(match: re.Match[str]) -> str:
    return match[1] if match[2].rstrip("/") == match[1] else match[0]


def _prose(text: str) -> str:
    return _ESCAPED.sub(r"\1", _AUTOLINK.sub(_unmarked, text))


def plain(text: str) -> str:
    kept, end = [], 0
    for code in _CODE.finditer(text):
        kept += [_prose(text[end : code.start()]), code[0]]
        end = code.end()
    return "".join(kept) + _prose(text[end:])


def _answered(feature: Feature) -> list[dict]:
    latest: dict[str, Record] = {}
    for story in feature.stories:
        for record in feature.records.get(story["id"], []):
            if record.kind == "decision-item":
                latest[record.subject] = record
    return [
        {
            "item": plain(str(r.data.get("item", r.subject))),
            "answer": plain(str(r.data["answer"])),
            "note": plain(str(r.data["note"])) if r.data.get("note") else r.data.get("note"),
        }
        for r in latest.values()
        if r.data.get("answer")
    ]


def gather(store: Store, feature: Feature, story: dict) -> Spec:
    description = story["description"] or ""
    shown = [planning.section(store, feature, name) for name in ("Contract", "Examples", "Invariants")]
    contract, examples, invariants = [None if body is None else plain(body) for body in shown]
    scope = f"{story['title']}\n\n{description}" if description else story["title"]
    after_merge = versions.after_merge(feature.records.get(story["id"], []))
    return Spec(contract, examples, invariants, plain(scope), _answered(feature), after_merge)


def _decision(entry: dict) -> str:
    note = f" ({entry['note']})" if entry["note"] else ""
    return f"- {entry['item']}: {entry['answer']}{note}"


def _blocks(parts: list[tuple[str, str | None]]) -> str:
    return "\n\n".join(f"# {name}\n\n{body or MISSING}" for name, body in parts) + "\n"


def text(spec: Spec) -> str:
    decisions = "\n".join(_decision(entry) for entry in spec.decisions)
    after_merge = "\n".join(f"- {item}" for item in spec.after_merge) or "none"
    return _blocks(
        [
            ("After merge", after_merge),
            ("Contract", spec.contract),
            ("Decisions", decisions),
            ("Examples", spec.examples),
            ("Invariants", spec.invariants),
            ("Scope", spec.scope),
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
        if used + _size(line) >= room:
            if not kept:
                start = line.encode("utf-8")[: room - used - 2].decode("utf-8", errors="ignore")
                kept.append(start + "\n")
            break
        used += _size(line)
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
