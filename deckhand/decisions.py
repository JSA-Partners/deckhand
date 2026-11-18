from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from deckhand import answers, ops
from deckhand.answers import Answer
from deckhand.questions import Question, as_tool_input, build
from deckhand.records import Record
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
    "decision-item": "Keep this decision item from",
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


def make_question(kind: str, subject: str) -> Question:
    if kind not in KINDS:
        raise Refusal(f"unknown decision kind {kind!r}, one of: {', '.join(KINDS)}")
    if not subject.strip():
        raise Refusal("a decision needs a subject")
    text = f"{_ASKS[kind]} {subject}? (decision kind: {kind})"
    return build(kind, subject, text, _OPTIONS[kind], multi=kind == "tier")


def save_pending(question: Question, version: str, stories: list[str], state: Path) -> None:
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
    given = [part for part in answer.split(", ")] if pending.kind == "tier" else [answer]
    return all(part in pending.options for part in given)


def _creates_record(pending: Pending, answer: str) -> bool:
    if pending.kind == "tier":
        return bool(answer.strip())
    return answer == _POSITIVE[pending.kind]


def check(pending: Pending, state: Path, agent_text: str = "") -> Answer:
    found = answers.find(pending.nonce, pending.question, state)
    if found is None:
        raise Refusal(f"no recorded answer for {pending.nonce}")
    if found.answer in pending.descriptions:
        raise Refusal(f"the answer for {pending.nonce} is an option description, not a choice")
    if agent_text and found.answer and not _offered(pending, found.answer) and found.answer in agent_text:
        raise Refusal(f"the answer for {pending.nonce} is copied from the agent text")
    return found


def decide(store: Store, pending: Pending, state: Path, agent_text: str = "") -> bool:
    found = check(pending, state, agent_text)
    if not _creates_record(pending, found.answer):
        return False
    record = Record(
        pending.kind,
        pending.subject,
        pending.version,
        f"person:{found.session_id}",
        {"answer": found.answer, "question": pending.question, "nonce": pending.nonce},
    )
    for story in pending.stories:
        ops.ensure_record(store, story, record)
    return True
