from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from deckhand.records import Record, content_hash
from deckhand.step import Refusal

KIND = "review-round"
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

    @property
    def id(self) -> str:
        parts = (self.lens, self.path, str(self.line), self.summary)
        return content_hash("\0".join(parts))[:12]


@dataclass(frozen=True)
class RoundPlan:
    n: int
    scoped: bool
    base_hash: str


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
    reason = item.get("reason", "")
    if not isinstance(reason, str):
        raise bad("reason", "must be a string")
    return Finding(
        **{field: item[field] for field in _TEXT},
        severity=item["severity"],
        line=line,
        confidence=float(confidence),
        reason=reason,
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


def round_record(story: str, stage: str, plan: RoundPlan, target_hash: str, findings: list[Finding]) -> Record:
    data = {
        "target_hash": target_hash,
        "findings": [asdict(f) | {"id": f.id} for f in findings],
        "scoped": plan.scoped,
        "base_hash": plan.base_hash,
    }
    return Record(KIND, f"{story}:{stage}:{plan.n}", target_hash, "agent:review", data)


def _accepted_p1(record: Record, declined: set[str]) -> bool:
    return any(f["severity"] == "P1" and f["id"] not in declined for f in record.data["findings"])


def next_round(rounds: list[Record], target_hash: str, declined: set[str]) -> RoundPlan | Stop:
    if not rounds:
        return RoundPlan(1, scoped=False, base_hash="")
    last = max(rounds, key=lambda r: int(r.subject.rsplit(":", 1)[1]))
    if last.data["target_hash"] == target_hash:
        return Stop("unchanged")
    if not _accepted_p1(last, declined):
        return Stop("clean")
    if len(rounds) >= CAP:
        return Stop("cap")
    return RoundPlan(len(rounds) + 1, scoped=True, base_hash=last.data["target_hash"])
