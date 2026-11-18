from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from deckhand import answers, features, ops, planning, review, tiers, versions
from deckhand.answers import Answer
from deckhand.questions import Question, as_tool_input, build
from deckhand.records import Record, content_hash
from deckhand.step import Refusal
from deckhand.store import Store

KINDS = (
    "contract",
    "invariants",
    "state-chart",
    "decision-item",
    "tier",
    "sitting",
    "lean",
    "feature-approval",
    "finding",
)

_OPTIONS = {
    "contract": ["Approve", "Not yet"],
    "invariants": ["Approve", "Not yet"],
    "state-chart": ["Approve", "Not yet"],
    "feature-approval": ["Approve", "Not yet"],
    "decision-item": ["Keep", "Change", "Discuss"],
    "sitting": ["Done", "Not yet"],
    "tier": tiers.OPTIONS,
    "lean": ["Prove it in Lean", "Not needed"],
    "finding": [review.DECLINE, review.ACCEPT],
}

_ASKS = {
    "contract": "Approve the contract for",
    "invariants": "Approve the invariants for",
    "state-chart": "Approve the state chart for",
    "feature-approval": "Approve the feature",
    "decision-item": "Keep the decision item",
    "sitting": "Is the sitting done for",
    "tier": "Which is the highest that applies to",
    "lean": "Is a Lean proof needed for",
    "finding": "Decline or accept the review finding on",
}

_POSITIVE = {
    "contract": "Approve",
    "invariants": "Approve",
    "state-chart": "Approve",
    "feature-approval": "Approve",
    "sitting": "Done",
    "lean": "Prove it in Lean",
}

PENDING = "pending"


@dataclass(frozen=True)
class Pending:
    nonce: str
    kind: str
    subject: str
    version: str
    stories: list[str]
    question: str
    options: list[str]
    descriptions: list[str]
    detail: str = ""
    label: str = ""
    story_labels: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Pin:
    subject: str
    version: str
    stories: list[str]
    detail: str = ""
    label: str = ""
    story_labels: list[str] = field(default_factory=list)


def make_question(kind: str, pin: Pin) -> Question:
    if kind not in KINDS:
        raise Refusal(f"unknown decision kind {kind!r}, one of: {', '.join(KINDS)}")
    if not pin.subject.strip():
        raise Refusal("a decision needs a subject")
    return build(kind, pin.label or pin.subject, question_text(kind, pin), _OPTIONS[kind])


def question_text(kind: str, pin: Pin) -> str:
    fields = {"kind": kind, **asdict(pin)}
    check = content_hash(json.dumps(fields, sort_keys=True))[:12]
    stories = ", ".join(pin.story_labels or pin.stories)
    where = f"version {content_hash(pin.version)[:12]}, stories {stories}, check {check}"
    shown = " ".join(part for part in (_ASKS[kind], pin.label) if part)
    if pin.detail:
        shown += f": {pin.detail}"
    return f"{shown} ({where})? (decision kind: {kind})"


def _label(story: dict) -> str:
    return f"{story['identifier']} {story['title']}"


def _finding_pin(store: Store, feature: features.Feature, ref: str, finding: str) -> Pin:
    story = features.story(store, feature, ref)
    found = review.locate(feature.records.get(story["id"], []), finding)
    if found is None:
        raise Refusal(f"{finding} is not a finding of the last review round on {story['identifier']}")
    round, item = found
    detail = f"{item['severity']} {item['lens']} {item['path']}:{item['line']} {item['summary']}"
    if round.subject.startswith(f"{feature.project['id']}:feature:"):
        live = feature.live
        labels = [_label(s) for s in live]
        return Pin(finding, round.version, [s["id"] for s in live], detail, feature.project["name"], labels)
    return Pin(finding, round.version, [story["id"]], detail, _label(story), [_label(story)])


def pin(store: Store, feature: features.Feature, kind: str, subject: str, stories: list[str], finding: str = "") -> Pin:
    if kind not in KINDS:
        raise Refusal(f"unknown decision kind {kind!r}, one of: {', '.join(KINDS)}")
    if (kind == "finding") != bool(finding):
        raise Refusal("finding needs --finding ID, and --finding applies only to finding")
    if kind != "decision-item" and stories:
        raise Refusal("--story applies only to decision-item; every other kind finds its own stories")
    if kind == "finding":
        return _finding_pin(store, feature, subject, finding)
    if kind == "decision-item":
        if not stories:
            raise Refusal("decision-item needs at least one --story")
        held = [features.story(store, feature, ref) for ref in stories]
        version = versions.version_for(store, feature, kind, subject)
        ids, labels = [s["id"] for s in held], [_label(s) for s in held]
        return Pin(versions.item_subject(subject), version, ids, subject, "", labels)
    if kind in versions.STORY_KINDS:
        story = features.story(store, feature, subject)
        version = versions.version_for(store, feature, kind, story["id"])
        return Pin(story["id"], version, [story["id"]], "", _label(story), [_label(story)])
    project = feature.project["id"]
    if kind in versions.SECTION_OF and planning.section(store, feature, versions.SECTION_OF[kind]) is None:
        raise Refusal(f"the {versions.SECTION_OF[kind]} section is not saved; save it before approving it")
    if subject != project:
        raise Refusal(f"{subject} is not the open feature; the open feature is {features.describe(feature.project)}")
    if kind == features.APPROVAL:
        gaps = features.approval_gaps(store, feature)
        if gaps:
            raise Refusal("the feature is not ready for approval: " + "; ".join(gaps[:3]))
    live = feature.live
    if not live:
        raise Refusal(f"{features.describe(feature.project)} has no stories to hold the decision")
    version = versions.version_for(store, feature, kind, project)
    return Pin(project, version, [s["id"] for s in live], "", feature.project["name"], [_label(s) for s in live])


def save_pending(question: Question, pin: Pin, state: Path) -> None:
    descriptions = [o["description"] for o in as_tool_input(question)["questions"][0]["options"]]
    pending = Pending(
        question.nonce,
        question.kind,
        pin.subject,
        pin.version,
        pin.stories,
        question.text,
        question.options,
        descriptions,
        pin.detail,
        pin.label,
        pin.story_labels,
    )
    folder = state / PENDING
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    fd = os.open(folder / f"{question.nonce}.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(pending)))


def pending_path(nonce: str, state: Path) -> Path:
    return state / PENDING / f"{nonce}.json"


def load_pending(nonce: str, state: Path) -> Pending:
    answers.check_nonce(nonce)
    try:
        return Pending(**json.loads(pending_path(nonce, state).read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise Refusal(f"no pending question for {nonce}") from None
    except (ValueError, TypeError):
        raise Refusal(f"the pending question for {nonce} is unreadable") from None


def _creates_record(pending: Pending, answer: str) -> bool:
    if pending.kind in ("tier", "decision-item", "finding"):
        return True
    return answer == _POSITIVE[pending.kind]


def _saw_the_options(pending: Pending, found: Answer) -> bool:
    shown = found.options
    if not isinstance(shown, list) or not all(isinstance(o, dict) for o in shown):
        return False
    labels, texts = [o.get("label") for o in shown], [o.get("description") for o in shown]
    return (labels, texts, found.multi) == (pending.options, pending.descriptions, False)


def check(pending: Pending, state: Path) -> Answer:
    found = answers.find(pending.nonce, pending.question, state)
    if found is None:
        raise Refusal(f"no recorded answer for {pending.nonce} in {state / answers.FILE}")
    pin = Pin(pending.subject, pending.version, pending.stories, pending.detail, pending.label, pending.story_labels)
    asked = question_text(pending.kind, pin)
    if pending.question != asked:
        raise Refusal(f"the pending question for {pending.nonce} differs from the question the person saw")
    if not _saw_the_options(pending, found):
        raise Refusal(f"the options the person saw for {pending.nonce} differ from the pending question")
    if found.copied:
        raise Refusal(f"the answer for {pending.nonce} is copied from recent agent output")
    if pending.kind == "tier":
        tiers.chosen(found.answer)
    if pending.kind == "finding" and found.answer not in pending.options:
        raise Refusal(f"the answer for {pending.nonce} is not one of {', '.join(pending.options)}")
    if found.answer in pending.descriptions:
        raise Refusal(f"the answer for {pending.nonce} is an option description, not a choice")
    return found


def _tier_data(answer: str) -> dict:
    tier = tiers.chosen(answer)
    return {"level": tier.level, "fact": tier.fact}


def decide(store: Store, pending: Pending, state: Path) -> bool:
    found = check(pending, state)
    if not _creates_record(pending, found.answer):
        return False
    record = Record(
        pending.kind,
        pending.subject,
        pending.version,
        f"person:{found.session_id}",
        {"answer": found.answer, "question": pending.question, "nonce": pending.nonce}
        | ({"item": pending.detail} if pending.kind == "decision-item" else {})
        | (_tier_data(found.answer) if pending.kind == "tier" else {}),
    )
    for story in pending.stories:
        ops.ensure_record(store, story, record)
    return True
