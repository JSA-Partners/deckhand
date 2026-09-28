"""Reading a findings, verdicts or decisions file into data.

The three files are JSON documents of the same shape: a `kind` naming which one it is, and a list
of entries under that same name. Nothing here touches GitHub: it is text in, data or `Refusal` out.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace

from deckhand.step import Refusal

CLEAN = "Nothing found."
SEVERITIES = ("P1", "P2", "P3")
PENDING = "PENDING"
VERDICTS = ("CONFIRMED", "REJECTED")
DECISIONS = ("accepted", "declined", "changed")
SHAPES = ("findings", "verdicts", "decisions")

_EntryReader = Callable[[object, int, list[str]], "tuple[str, str, str] | None"]


@dataclass(frozen=True)
class Finding:
    """One finding: the reviewer's entry, with the skeptic's verdict on it."""

    lens: str
    ordinal: int
    severity: str
    claim: str
    evidence: str
    verdict: str = PENDING
    rejection: str = ""

    @property
    def id(self) -> str:
        return f"{self.lens}.{self.ordinal}"


def _entries(text: str, kind: str) -> list[object]:
    """The list of entries a `kind` document carries; a malformed document is a `Refusal`, not a guess."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise Refusal(f"line {error.lineno} column {error.colno}: {error.msg}") from None
    if not isinstance(document, dict) or "kind" not in document:
        raise Refusal(f'a {kind} file opens with {{"kind": "{kind}", "{kind}": [...]}}')
    if document["kind"] != kind:
        raise Refusal(f"expected a {kind} file, got {document['kind']}; the order is {', '.join(SHAPES)}")
    if not isinstance(document.get(kind), list):
        raise Refusal(f"'{kind}' must be a list")
    return document[kind]


def _missing(entry: object, keys: tuple[str, ...], position: int, faults: list[str]) -> bool:
    """Whether `entry` is unusable: not an object, or missing one of `keys`; each absence gets its own fault."""
    if not isinstance(entry, dict):
        faults.append(f"entry {position}: not an object")
        return True
    ok = True
    for key in keys:
        if key not in entry:
            faults.append(f"entry {position}: missing '{key}'")
            ok = False
    return not ok


def _by_finding(
    entries: list[object], found: list[Finding], read: _EntryReader, noun: str
) -> dict[str, tuple[str, str]]:
    """One `(word, reason)` per finding id, read one entry at a time by `read`; every finding gets one, and only
    findings do."""
    ids = {finding.id for finding in found}
    joined: dict[str, tuple[str, str]] = {}
    faults: list[str] = []
    for position, entry in enumerate(entries, start=1):
        parsed = read(entry, position, faults)
        if parsed is None:
            continue
        ref, word, reason = parsed
        if ref not in ids:
            faults.append(f"entry {position}: {ref} is not a finding")
        elif ref in joined:
            faults.append(f"entry {position}: {ref} already has a {noun}")
        else:
            joined[ref] = (word, reason)
    if faults:
        raise Refusal("\n".join(faults))
    missing = [finding.id for finding in found if finding.id not in joined]
    if missing:
        raise Refusal(f"no {noun} for {', '.join(missing)}")
    return joined


def _verdict_entry(entry: object, position: int, faults: list[str]) -> tuple[str, str, str] | None:
    if _missing(entry, ("id", "verdict"), position, faults):
        return None
    ref, verdict = entry["id"], entry["verdict"]
    if verdict not in VERDICTS:
        faults.append(f"entry {position}: verdict must be one of {', '.join(VERDICTS)}, got {verdict!r}")
        return None
    reason = entry.get("reason", "")
    if verdict == "REJECTED" and not reason:
        faults.append(f"entry {position}: a REJECTED verdict needs a reason")
        return None
    return ref, verdict, reason if verdict == "REJECTED" else ""


def _decision_entry(entry: object, position: int, faults: list[str]) -> tuple[str, str, str] | None:
    if _missing(entry, ("id", "decision"), position, faults):
        return None
    ref, decision = entry["id"], entry["decision"]
    if decision not in DECISIONS:
        faults.append(f"entry {position}: decision must be one of {', '.join(DECISIONS)}, got {decision!r}")
        return None
    return ref, decision, entry.get("reason", "")


def verdicts(text: str, found: list[Finding]) -> dict[str, tuple[str, str]]:
    """The skeptic's verdict and reason by finding id."""
    return _by_finding(_entries(text, "verdicts"), found, _verdict_entry, "verdict")


def decisions(text: str, found: list[Finding]) -> dict[str, tuple[str, str]]:
    """The person's decision and reason by finding id."""
    return _by_finding(_entries(text, "decisions"), found, _decision_entry, "decision")


def judged(found: list[Finding], text: str) -> list[Finding]:
    """The findings with the skeptic's verdicts on them, in the reviewer's order."""
    by_id = verdicts(text, found)
    return [replace(f, verdict=by_id[f.id][0], rejection=by_id[f.id][1]) for f in found]


def findings(text: str, known: set[str]) -> list[Finding]:
    """Every finding in `text`; an entry out of the stated shape, or naming an unknown lens, is a refusal."""
    entries = _entries(text, "findings")
    parsed: list[Finding] = []
    seen: set[str] = set()
    faults: list[str] = []
    for position, entry in enumerate(entries, start=1):
        if _missing(entry, ("lens", "ordinal", "severity", "claim", "evidence"), position, faults):
            continue
        lens, severity = entry["lens"], entry["severity"]
        if severity not in SEVERITIES:
            faults.append(f"entry {position}: severity must be one of {', '.join(SEVERITIES)}, got {severity!r}")
            continue
        if lens not in known:
            faults.append(f"entry {position}: no lens named '{lens}'")
            continue
        found = Finding(lens, entry["ordinal"], severity, entry["claim"], entry["evidence"])
        if found.id in seen:
            faults.append(f"entry {position}: {found.id} is already the id of an earlier finding")
            continue
        seen.add(found.id)
        parsed.append(found)
    if faults:
        raise Refusal("\n".join(faults))
    return parsed
