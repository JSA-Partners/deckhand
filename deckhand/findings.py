"""Reading a findings, verdicts or decisions file into data.

The three files are three positional arguments of the same shape, so every refusal here says which
line failed and, when the whole file parses as one of the other two, which shape it actually reads
as. Nothing here touches GitHub: it is text in, data or `Refusal` out.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from deckhand.step import Refusal

CLEAN = "Nothing found."
SEVERITIES = ("P1", "P2", "P3")
PENDING = "PENDING"
VERDICTS = ("CONFIRMED", "REJECTED")
DECISIONS = ("accepted", "declined", "changed")

_EXPECTED = f"expected <lens>.<n> | P1|P2|P3 | {PENDING} | <claim> | <evidence>"
_EXPECTED_VERDICT = "expected <lens>.<n> | CONFIRMED, or <lens>.<n> | REJECTED | <reason>"
_EXPECTED_DECISION = "expected <lens>.<n> | accepted|declined|changed [| <reason>]"


@dataclass(frozen=True)
class Finding:
    """One finding: the reviewer's line split into its columns, with the skeptic's verdict on it."""

    lens: str
    ordinal: int
    severity: str
    verdict: str
    claim: str
    evidence: str
    rejection: str = ""

    @property
    def id(self) -> str:
        return f"{self.lens}.{self.ordinal}"


def _id(ref: str) -> tuple[str, int] | None:
    """`(lens, ordinal)` from `<lens>.<n>`, or None when it is not one."""
    lens, dot, ordinal = ref.partition(".")
    if not lens or not dot or not (ordinal.isascii() and ordinal.isdigit()):
        return None
    return lens, int(ordinal)


def _fields(line: str, *wanted: int) -> list[str]:
    """The line's fields: `" | "` when that gives one of `wanted`, else the bare `"|"`.

    A claim may hold a pipe, a regex alternation for one, and splitting on the bare character takes
    half the claim into the next field and refuses a line the brief permitted. The bare split stays
    as the fallback, so a line written without the spaces around its separators still reads.
    """
    spaced = [part.strip() for part in line.split(" | ")]
    if len(spaced) in wanted:
        return spaced
    return [part.strip() for part in line.split("|")]


def _parse(line: str) -> Finding | None:
    """One findings line as a `Finding`, or None when it is not in the format the brief stated."""
    parts = _fields(line, 5)
    if len(parts) != 5:
        return None
    ref, severity, verdict, claim, evidence = parts
    found = _id(ref)
    if found is None or severity not in SEVERITIES or verdict != PENDING or not claim or not evidence:
        return None
    return Finding(*found, severity, verdict, claim, evidence)


def _parse_verdict(line: str) -> tuple[str, str, str] | None:
    """`(id, verdict, reason)` from one verdict line, or None; a rejection has to say why."""
    parts = _fields(line, 2, 3)
    if len(parts) not in (2, 3) or _id(parts[0]) is None or parts[1] not in VERDICTS:
        return None
    reason_text = parts[2] if len(parts) == 3 else ""
    if parts[1] == "REJECTED" and not reason_text:
        return None
    return parts[0], parts[1], reason_text if parts[1] == "REJECTED" else ""


def _parse_decision(line: str) -> tuple[str, str, str] | None:
    """`(id, decision, reason)` from one decision line, or None; the reason is the person's and optional."""
    parts = _fields(line, 2, 3)
    if len(parts) not in (2, 3) or _id(parts[0]) is None or parts[1] not in DECISIONS:
        return None
    return parts[0], parts[1], parts[2] if len(parts) == 3 else ""


SHAPES = ("findings", "verdicts", "decisions")


def _reads_as(text: str, passed: str) -> str | None:
    """The one of `SHAPES` every line of `text` parses as, when that is not `passed`; else None.

    The three files are three positional arguments of the same shape, so a swap arrives as a format
    error about one line. Naming the shape the whole file does read as turns that into the mistake
    it is, and the order is what the person needs to hear next.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    for shape, parse in zip(SHAPES, (_parse, _parse_verdict, _parse_decision), strict=True):
        if shape != passed and all(parse(line) is not None for line in lines):
            return shape
    return None


def _refusal(number: int, expected: str, text: str, passed: str) -> Refusal:
    """The format refusal for one line, saying which of the three shapes the whole file reads as."""
    found = _reads_as(text, passed)
    hint = f"; that file reads as {found}, and the order is findings, verdicts, decisions" if found else ""
    return Refusal(f"line {number}: {expected}{hint}")


def _by_finding(
    text: str, found: list[Finding], parse: Callable[[str], tuple[str, str, str] | None], noun: str, expected: str
) -> dict[str, tuple[str, str]]:
    """One `(word, reason)` per finding id from `text`, read with `parse`; every finding gets one, and only findings do.

    The verdicts and the decisions are the same shape read from two files, so one reader holds the
    rules and each caller names only its parse and its noun for the refusals.
    """
    ids = {finding.id for finding in found}
    read: dict[str, tuple[str, str]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parsed = parse(line)
        if parsed is None:
            raise _refusal(number, expected, text, f"{noun}s")
        ref, word, why = parsed
        if ref not in ids:
            raise Refusal(f"line {number}: {ref} is not a finding")
        if ref in read:
            raise Refusal(f"line {number}: {ref} already has a {noun}")
        read[ref] = (word, why)
    missing = [finding.id for finding in found if finding.id not in read]
    if missing:
        raise Refusal(f"no {noun} for {', '.join(missing)}")
    return read


def verdicts(text: str, found: list[Finding]) -> dict[str, tuple[str, str]]:
    """The skeptic's verdict and reason by finding id."""
    return _by_finding(text, found, _parse_verdict, "verdict", _EXPECTED_VERDICT)


def decisions(text: str, found: list[Finding]) -> dict[str, tuple[str, str]]:
    """The person's decision and reason by finding id."""
    return _by_finding(text, found, _parse_decision, "decision", _EXPECTED_DECISION)


def judged(found: list[Finding], text: str) -> list[Finding]:
    """The findings with the skeptic's verdicts on them, in the reviewer's order."""
    by_id = verdicts(text, found)
    return [replace(f, verdict=by_id[f.id][0], rejection=by_id[f.id][1]) for f in found]


def findings(text: str, known: set[str]) -> list[Finding]:
    """Every finding in `text`; a line that is not in the stated format is a refusal, not a guess."""
    if text.strip() == CLEAN:
        return []
    parsed: list[Finding] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        found = _parse(line)
        if found is None:
            raise _refusal(number, _EXPECTED, text, "findings")
        if found.lens not in known:
            raise Refusal(f"line {number}: no lens named '{found.lens}'")
        if found.id in seen:
            raise Refusal(f"line {number}: {found.id} is already the id of an earlier finding")
        seen.add(found.id)
        parsed.append(found)
    if not parsed:
        raise Refusal(f"the findings file is empty; write the findings, or exactly `{CLEAN}`")
    return parsed
