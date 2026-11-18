from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from deckhand import answers, features, ops, planning, versions
from deckhand.answers import Answer
from deckhand.questions import Question, as_tool_input, build
from deckhand.records import Record, content_hash
from deckhand.step import Refusal
from deckhand.store import Store
from deckhand.tiers import FACTS

KINDS = ("contract", "invariants", "state-chart", "decision-item", "tier", "sitting", "feature-approval")

_OPTIONS = {
    "contract": ["Approve", "Not yet"],
    "invariants": ["Approve", "Not yet"],
    "state-chart": ["Approve", "Not yet"],
    "feature-approval": ["Approve", "Not yet"],
    "decision-item": ["Keep", "Change", "Discuss"],
    "sitting": ["Done", "Not yet"],
    "tier": [fact.question for fact in FACTS],
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
    "decision-item": "Keep",
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


@dataclass(frozen=True)
class Pin:
    subject: str
    version: str
    stories: list[str]
    detail: str = ""


def make_question(kind: str, subject: str, version: str, stories: list[str], detail: str = "") -> Question:
    if kind not in KINDS:
        raise Refusal(f"unknown decision kind {kind!r}, one of: {', '.join(KINDS)}")
    if not subject.strip():
        raise Refusal("a decision needs a subject")
    text = question_text(kind, subject, version, stories, detail)
    return build(kind, subject, text, _OPTIONS[kind], multi=kind == "tier")


def question_text(kind: str, subject: str, version: str, stories: list[str], detail: str = "") -> str:
    where = f"version {content_hash(version)[:12]}, stories {', '.join(stories)}"
    shown = f"{subject}: {detail}" if detail else subject
    return f"{_ASKS[kind]} {shown} ({where})? (decision kind: {kind})"


def pin(store: Store, feature: features.Feature, kind: str, subject: str, stories: list[str]) -> Pin:
    if kind not in KINDS:
        raise Refusal(f"unknown decision kind {kind!r}, one of: {', '.join(KINDS)}")
    if kind != "decision-item" and stories:
        raise Refusal("--story applies only to decision-item; every other kind finds its own stories")
    if kind == "decision-item":
        if not stories:
            raise Refusal("decision-item needs at least one --story")
        ids = [features.story(store, feature, ref)["id"] for ref in stories]
        version = versions.version_for(store, feature, kind, subject)
        return Pin(versions.item_subject(subject), version, ids, subject)
    if kind in versions.STORY_KINDS:
        story = features.story(store, feature, subject)["id"]
        return Pin(story, versions.version_for(store, feature, kind, story), [story])
    project = feature.project["id"]
    if kind in versions.SECTION_OF and planning.section(store, feature, versions.SECTION_OF[kind]) is None:
        raise Refusal(f"the {versions.SECTION_OF[kind]} section is not saved; save it before approving it")
    if subject != project:
        raise Refusal(f"{subject} is not the open feature; the open feature is {features.describe(feature.project)}")
    if kind == features.APPROVAL:
        gaps = features.approval_gaps(store, feature)
        if gaps:
            raise Refusal("the feature is not ready for approval: " + "; ".join(gaps[:3]))
    live = [s["id"] for s in feature.live]
    if not live:
        raise Refusal(f"{features.describe(feature.project)} has no stories to hold the decision")
    return Pin(project, versions.version_for(store, feature, kind, project), live)


def save_pending(question: Question, version: str, stories: list[str], state: Path, detail: str = "") -> None:
    descriptions = [o["description"] for o in as_tool_input(question)["questions"][0]["options"]]
    pending = Pending(
        question.nonce,
        question.kind,
        question.subject,
        version,
        stories,
        question.text,
        question.options,
        descriptions,
        detail,
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
    asked = question_text(pending.kind, pending.subject, pending.version, pending.stories, pending.detail)
    if pending.question != asked:
        raise Refusal(f"the pending question for {pending.nonce} differs from the question the person saw")
    if found.copied:
        raise Refusal(f"the answer for {pending.nonce} is copied from recent agent output")
    if pending.kind == "tier" and not _offered(pending, found.answer):
        raise Refusal(f"the answer for {pending.nonce} is not a selection of the offered options")
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
        | ({"item": pending.detail} if pending.detail else {}),
    )
    for story in pending.stories:
        ops.ensure_record(store, story, record)
    return True
