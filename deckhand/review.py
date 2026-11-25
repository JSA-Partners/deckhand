from __future__ import annotations

import difflib
import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from deckhand.records import Record, content_hash
from deckhand.step import Refusal

KIND = "review-round"
DECISION = "finding"
HAND_ACCEPT = "finding-accept"
PROPOSAL = "decline-proposal"
CAP_DECISION = "review-cap"
PROCEED, REWORK = "Proceed", "Rework"
DECLINE, ACCEPT = "Decline", "Accept"
PERSON = "person:"
TEXTS = "plans"
CAP = 3
DOUBTFUL = 0.5
SEVERITIES = ("P1", "P2", "P3")
_TEXT = ("lens", "path", "summary", "fix", "evidence")


@dataclass(frozen=True)
class Finding:
    lens: str
    severity: str
    path: str
    line: int
    summary: str
    fix: str
    evidence: str
    confidence: float
    reason: str = ""
    changes_delivery: bool = False
    touches: str = ""


def finding_id(finding: Finding, stage: str) -> str:
    parts = (stage, finding.lens, finding.path, str(finding.line), finding.summary)
    return content_hash("\0".join(parts))[:12]


@dataclass(frozen=True)
class RoundPlan:
    n: int
    scoped: bool
    base_hash: str
    carry: bool = False


@dataclass(frozen=True)
class Stop:
    reason: str


def _finding(index: int, item: object) -> Finding:
    def bad(field: str, rule: str) -> Refusal:
        return Refusal(f"finding {index}: {field} {rule}")

    if not isinstance(item, dict):
        raise Refusal(f"finding {index} is not an object")
    for field in _TEXT:
        if not isinstance(item.get(field), str):
            raise bad(field, "must be a string")
    if item.get("severity") not in SEVERITIES:
        raise bad("severity", "must be P1, P2 or P3")
    line = item.get("line")
    if not isinstance(line, int) or isinstance(line, bool) or line < 0:
        raise bad("line", "must be an integer of 0 or more")
    confidence = item.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise bad("confidence", "must be a number from 0.0 to 1.0")
    reason, touches = item.get("reason", ""), item.get("touches", "")
    for field, value in (("reason", reason), ("touches", touches)):
        if not isinstance(value, str):
            raise bad(field, "must be a string")
    changes_delivery = item.get("changes_delivery", False)
    if not isinstance(changes_delivery, bool):
        raise bad("changes_delivery", "must be true or false")
    return Finding(
        **{field: item[field] for field in _TEXT},
        severity=item["severity"],
        line=line,
        confidence=float(confidence),
        reason=reason,
        changes_delivery=changes_delivery,
        touches=touches,
    )


def parse_findings(text: str) -> list[Finding]:
    try:
        items = json.loads(text)
    except ValueError:
        raise Refusal("findings are not valid JSON") from None
    if not isinstance(items, list):
        raise Refusal("findings must be a JSON list")
    return [_finding(index, item) for index, item in enumerate(items)]


def ordered(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (f.confidence < DOUBTFUL, -f.confidence))


def round_record(
    story: str,
    stage: str,
    plan: RoundPlan,
    target_hash: str,
    findings: list[Finding],
    head: str = "",
    carried: list[str] | None = None,
) -> Record:
    data = {
        "target_hash": target_hash,
        "findings": [asdict(f) | {"id": finding_id(f, stage)} for f in findings],
        "scoped": plan.scoped,
        "base_hash": plan.base_hash,
    }
    if head:
        data["head"] = head
    if carried:
        data["carried"] = carried
    return Record(KIND, f"{story}:{stage}:{plan.n}", target_hash, "agent:review", data)


def round_number(record: Record) -> int:
    return int(record.subject.rsplit(":", 1)[1])


def rounds_of(records: list[Record], prefix: str) -> list[Record]:
    found = {r.subject: r for r in records if r.kind == KIND and r.subject.rsplit(":", 1)[0] == prefix}
    return sorted(found.values(), key=round_number)


def last(rounds: list[Record]) -> Record | None:
    return max(rounds, key=round_number) if rounds else None


def decided(records: list[Record]) -> dict[str, str]:
    return {r.subject: r.data.get("answer") for r in records if r.kind == DECISION and r.author.startswith(PERSON)}


def declined(records: list[Record]) -> set[str]:
    return {id for id, answer in decided(records).items() if answer == DECLINE}


def accepted(records: list[Record]) -> set[str]:
    by_person = {id for id, answer in decided(records).items() if answer == ACCEPT}
    return by_person | {r.subject for r in records if r.kind == HAND_ACCEPT}


def _fixed(rounds: list[Record], id: str) -> bool:
    raised = max(n for n, round in enumerate(rounds) if any(f["id"] == id for f in round.data["findings"]))
    later = rounds[raised + 1 :]
    return any(not round.data["scoped"] or id in round.data.get("carried", []) for round in later)


def _unfixed(records: list[Record], prefix: str) -> list[dict]:
    answers = decided(records)
    rounds = rounds_of(records, prefix)
    found: dict[str, dict] = {}
    for round in rounds:
        for item in round.data["findings"]:
            if answers.get(item["id"]) == ACCEPT and item["id"] not in found and not _fixed(rounds, item["id"]):
                found[item["id"]] = item
    return list(found.values())


def unfixed(records: list[Record]) -> list[dict]:
    prefixes = dict.fromkeys(r.subject.rsplit(":", 1)[0] for r in records if r.kind == KIND)
    return [item for prefix in prefixes for item in _unfixed(records, prefix)]


def carried(records: list[Record], prefix: str) -> list[dict]:
    held = last(counted(records, prefix)[0])
    p1 = open_p1(held, excused(records, prefix)) if held else []
    ids = {f["id"] for f in p1}
    return p1 + [f for f in _unfixed(records, prefix) if f["id"] not in ids]


def accept_record(finding: str, version: str) -> Record:
    return Record(HAND_ACCEPT, finding, version, "agent:review", {"answer": ACCEPT})


def locate(records: list[Record], finding: str) -> tuple[Record, dict] | None:
    prefixes = sorted({r.subject.rsplit(":", 1)[0] for r in records if r.kind == KIND})
    for prefix in prefixes:
        for round in reversed(rounds_of(records, prefix)):
            for item in round.data["findings"]:
                if item["id"] == finding:
                    return round, item
    return None


def open_findings(record: Record, declined: set[str]) -> list[dict]:
    return [f for f in record.data["findings"] if f["id"] not in declined]


def open_p1(record: Record, declined: set[str]) -> list[dict]:
    return [f for f in open_findings(record, declined) if f["severity"] == "P1"]


def carry_record(story: str, stage: str, plan: RoundPlan, last: Record, head: str, target_hash: str = "") -> Record:
    target = target_hash or last.data["target_hash"]
    base = last.data["target_hash"]
    data = {"target_hash": target, "findings": last.data["findings"], "scoped": True, "base_hash": base}
    data |= {"head": head, "carry": True}
    return Record(KIND, f"{story}:{stage}:{plan.n}", target, "agent:review", data)


# A sync alone changes the whole diff and leaves the story's own change empty.
def carry_when_empty(plan: RoundPlan, scoped_diff: str | None) -> RoundPlan:
    return replace(plan, carry=True) if plan.scoped and scoped_diff == "" else plan


def p1_rounds(rounds: list[Record], excused: set[str], since: int = 0) -> int:
    return sum(round_number(r) > since and not r.data.get("carry") and bool(open_p1(r, excused)) for r in rounds)


def next_round(
    rounds: list[Record], target_hash: str, excused: set[str], head: str = "", since: int = 0
) -> RoundPlan | Stop:
    latest = last(rounds)
    if latest is None:
        return RoundPlan(1, scoped=False, base_hash="")
    moved = bool(head and latest.data.get("head") and latest.data["head"] != head)
    if latest.data["target_hash"] == target_hash and not moved:
        return Stop("unchanged") if open_p1(latest, excused) else Stop("clean")
    if at_cap(rounds, excused, since):
        return Stop("cap")
    if latest.data["target_hash"] == target_hash:
        return RoundPlan(len(rounds) + 1, scoped=True, base_hash=target_hash, carry=True)
    return RoundPlan(len(rounds) + 1, scoped=True, base_hash=latest.data["target_hash"])


def reviewed(record: Record) -> str:
    return record.data.get("head") or record.data["target_hash"]


def cap_version(round: Record) -> str:
    return f"{round.subject}:{reviewed(round)}"


def _cap_cut(record: Record) -> int:
    try:
        return int(record.version.split(":")[2])
    except (IndexError, ValueError):
        return 0


# Each cap answer is its own record, named by the round it was given at.
def cap_subject(round: Record) -> str:
    return round.subject


def cap_prefix(subject: str) -> str:
    return subject.rpartition(":")[0]


def cap_answers(records: list[Record], prefix: str) -> list[Record]:
    return [
        r for r in records if r.kind == CAP_DECISION and cap_prefix(r.subject) == prefix and r.author.startswith(PERSON)
    ]


def _cap_answers(records: list[Record], prefix: str, answer: str) -> list[Record]:
    return [r for r in cap_answers(records, prefix) if r.data.get("answer") == answer]


def counted(records: list[Record], prefix: str) -> tuple[list[Record], int]:
    rounds = rounds_of(records, prefix)
    stage = prefix.rpartition(":")[2]
    if stage not in ("plan", "branch"):
        return rounds, 0
    cut = max(map(_cap_cut, _cap_answers(records, prefix, REWORK)), default=0)
    return [r for r in rounds if round_number(r) > cut], cut


def waived(records: list[Record], prefix: str) -> set[str]:
    pinned = {r.version for r in _cap_answers(records, prefix, PROCEED)}
    rounds = [r for r in counted(records, prefix)[0] if cap_version(r) in pinned]
    return {f["id"] for r in rounds for f in open_p1(r, declined(records))}


def waived_anywhere(records: list[Record]) -> set[str]:
    prefixes = dict.fromkeys(r.subject.rsplit(":", 1)[0] for r in records if r.kind == KIND)
    return {id for prefix in prefixes for id in waived(records, prefix)}


def excused(records: list[Record], prefix: str) -> set[str]:
    return declined(records) | waived(records, prefix)


def proceeded(records: list[Record], prefix: str) -> int:
    return max(map(_cap_cut, _cap_answers(records, prefix, PROCEED)), default=0)


def at_cap(rounds: list[Record], excused: set[str], since: int = 0) -> bool:
    latest = last(rounds)
    return latest is not None and bool(open_p1(latest, excused)) and p1_rounds(rounds, excused, since) >= CAP


def cleared(records: list[Record], story: str, stage: str, at: str) -> bool:
    prefix = f"{story}:{stage}"
    latest = last(counted(records, prefix)[0])
    return latest is not None and reviewed(latest) == at and not open_p1(latest, excused(records, prefix))


def text_path(state: Path, subject: str, version: str) -> Path:
    return state / TEXTS / subject / f"{version}.md"


def save_text(state: Path, subject: str, version: str, text: str) -> Path:
    path = text_path(state, subject, version)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for folder in (state, state / TEXTS, path.parent):
        folder.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(text.encode())
    return path


def saved_text(state: Path, subject: str, version: str) -> str | None:
    try:
        return text_path(state, subject, version).read_bytes().decode()
    except (OSError, UnicodeDecodeError):
        return None


def text_diff(old: str, new: str) -> str:
    lines = difflib.unified_diff(old.splitlines(True), new.splitlines(True), "reviewed", "revised")
    return "".join(lines)
