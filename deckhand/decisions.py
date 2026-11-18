from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from deckhand import answers, features, ops, planning, tiers, versions
from deckhand.answers import Answer
from deckhand.questions import Question, as_tool_input, build
from deckhand.records import Record, content_hash
from deckhand.step import Refusal
from deckhand.store import Store

KINDS = ("contract", "invariants", "state-chart", "decision-item", "tier", "sitting", "feature-approval")

_OPTIONS = {
    "contract": ["Approve", "Not yet"],
    "invariants": ["Approve", "Not yet"],
    "state-chart": ["Approve", "Not yet"],
    "feature-approval": ["Approve", "Not yet"],
    "decision-item": ["Keep", "Change", "Discuss"],
    "sitting": ["Done", "Not yet"],
    "tier": tiers.OPTIONS,
}

_ASKS = {
    "contract": "Approve the contract for",
    "invariants": "Approve the invariants for",
    "state-chart": "Approve the state chart for",
    "feature-approval": "Approve the feature",
    "decision-item": "Keep the decision item",
    "sitting": "Is the sitting done for",
    "tier": "Which of these apply to",
}

_POSITIVE = {
    "contract": "Approve",
    "invariants": "Approve",
    "state-chart": "Approve",
    "feature-approval": "Approve",
    "sitting": "Done",
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
    return build(kind, pin.label or pin.subject, question_text(kind, pin), _OPTIONS[kind], multi=kind == "tier")


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


def pin(store: Store, feature: features.Feature, kind: str, subject: str, stories: list[str]) -> Pin:
    if kind not in KINDS:
        raise Refusal(f"unknown decision kind {kind!r}, one of: {', '.join(KINDS)}")
    if kind != "decision-item" and stories:
        raise Refusal("--story applies only to decision-item; every other kind finds its own stories")
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
    try:
        return Pending(**json.loads(pending_path(nonce, state).read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise Refusal(f"no pending question for {nonce}") from None
    except (ValueError, TypeError):
        raise Refusal(f"the pending question for {nonce} is unreadable") from None


def _offered(pending: Pending, answer: str) -> bool:
    return answers.only_labels(answer, pending.options)


def _creates_record(pending: Pending, answer: str) -> bool:
    if pending.kind in ("tier", "decision-item"):
        return True
    return answer == _POSITIVE[pending.kind]


def check(pending: Pending, state: Path) -> Answer:
    found = answers.find(pending.nonce, pending.question, state)
    if found is None:
        raise Refusal(f"no recorded answer for {pending.nonce}")
    pin = Pin(pending.subject, pending.version, pending.stories, pending.detail, pending.label, pending.story_labels)
    asked = question_text(pending.kind, pin)
    if pending.question != asked:
        raise Refusal(f"the pending question for {pending.nonce} differs from the question the person saw")
    if found.copied:
        raise Refusal(f"the answer for {pending.nonce} is copied from recent agent output")
    if pending.kind == "tier" and not _offered(pending, found.answer):
        raise Refusal(f"the answer for {pending.nonce} is not a selection of the offered options")
    if pending.kind == "tier":
        tiers.chosen(found.answer)
    if found.answer in pending.descriptions:
        raise Refusal(f"the answer for {pending.nonce} is an option description, not a choice")
    return found


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
        | ({"item": pending.detail} if pending.detail else {})
        | ({"facts": sorted(tiers.chosen(found.answer))} if pending.kind == "tier" else {}),
    )
    for story in pending.stories:
        ops.ensure_record(store, story, record)
    return True
