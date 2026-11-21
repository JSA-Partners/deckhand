from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from deckhand import answers, asks, ops, quiz, review, signoff, tiers
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
    review.CAP_DECISION,
    quiz.KIND,
    signoff.KIND,
    signoff.SECOND,
    signoff.AFTER,
    asks.ANSWER,
    asks.EDIT,
)

_PIN_OPTIONS = (quiz.KIND, asks.ANSWER, signoff.KIND)

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
    review.CAP_DECISION: [review.PROCEED, review.REWORK],
    signoff.KIND: [signoff.SIGN, signoff.NOT_YET],
    signoff.SECOND: [signoff.SIGN, signoff.NOT_YET],
    signoff.AFTER: [signoff.DONE, signoff.NOT_YET],
    asks.EDIT: asks.EDIT_OPTIONS,
}

_SIGN_OFF = {
    signoff.SIGN: "the hand merges it, pinned to this head",
    signoff.NOT_YET: "the hand changes what you name",
    signoff.BACK: "nothing is recorded",
}

_DESCRIPTIONS = {
    "decision-item": ["the item stands as written", "say what changes", "talk it through first"],
    "finding": ["the finding stays unfixed; the hand's call stands", "the hand fixes the finding"],
    review.CAP_DECISION: ["carry on past the cap", "the hand reworks it and the rounds count again"],
    signoff.SECOND: ["the hand merges it, pinned to the signed head", "the story waits for a new sign-off"],
    signoff.AFTER: ["the item is done", "the item stays in your queue"],
    asks.EDIT: ["you made the change", "the hand carries on without it"],
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
    review.CAP_DECISION: "Three plan review rounds ran, none clean. Proceed to the build or rework the plan for",
    quiz.KIND: "Quiz on",
    signoff.KIND: "Sign off",
    signoff.SECOND: "Second sign-off on",
    signoff.AFTER: "Is this after-merge item done for",
    asks.ANSWER: "A hand asks on",
    asks.EDIT: "Make this change to Claude Code's own config for",
}
_BRANCH_CAP = "Three branch review rounds ran, none clean. Proceed to sign-off or rework the branch for"
_RECORDED = (
    "tier",
    "decision-item",
    "finding",
    review.CAP_DECISION,
    quiz.KIND,
    signoff.KIND,
    signoff.SECOND,
    asks.ANSWER,
    asks.EDIT,
)
_CHOSEN = ("finding", review.CAP_DECISION, quiz.KIND, signoff.KIND, signoff.SECOND, signoff.AFTER, asks.EDIT)

_POSITIVE = {
    "contract": "Approve",
    "invariants": "Approve",
    "state-chart": "Approve",
    "feature-approval": "Approve",
    "sitting": "Done",
    "lean": "Prove it in Lean",
    signoff.AFTER: signoff.DONE,
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
    options: list[str] = field(default_factory=list)


def make_question(kind: str, pin: Pin) -> Question:
    if kind not in KINDS:
        raise Refusal(f"unknown decision kind {kind!r}, one of: {', '.join(KINDS)}")
    if not pin.subject.strip():
        raise Refusal("a decision needs a subject")
    options = (pin.options or _OPTIONS.get(kind, [])) if kind in _PIN_OPTIONS else _OPTIONS[kind]
    descriptions = [_SIGN_OFF.get(o, "") for o in options] if kind == signoff.KIND else _DESCRIPTIONS.get(kind)
    return build(kind, pin.label or pin.subject, question_text(kind, pin), options, descriptions)


def question_text(kind: str, pin: Pin) -> str:
    fields = {"kind": kind, **asdict(pin)}
    check = content_hash(json.dumps(fields, sort_keys=True))[:12]
    ask = _BRANCH_CAP if kind == review.CAP_DECISION and pin.subject.endswith(":branch") else _ASKS[kind]
    shown = " ".join(part for part in (ask, pin.label) if part)
    if pin.detail:
        shown += f": {pin.detail}"
    stories = pin.story_labels or pin.stories
    if stories != [pin.label]:
        shown += f" (stories {', '.join(stories)})"
    return f"{shown.rstrip('?')}? [{check}]"


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
    if pending.kind == signoff.KIND and answer == signoff.BACK:
        return False
    if pending.kind in _RECORDED:
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
    options = pending.options if pending.kind in _PIN_OPTIONS and pending.options != _OPTIONS.get(pending.kind) else []
    pin = Pin(
        pending.subject, pending.version, pending.stories, pending.detail, pending.label, pending.story_labels, options
    )
    asked = question_text(pending.kind, pin)
    if pending.question != asked:
        raise Refusal(f"the pending question for {pending.nonce} differs from the question the person saw")
    if not _saw_the_options(pending, found):
        raise Refusal(f"the options the person saw for {pending.nonce} differ from the pending question")
    if found.copied:
        raise Refusal(f"the answer for {pending.nonce} is copied from recent agent output")
    if pending.kind == "tier":
        tiers.chosen(found.answer)
    if pending.kind in _CHOSEN and found.answer not in pending.options:
        raise Refusal(f"the answer for {pending.nonce} is not one of {', '.join(pending.options)}")
    if found.answer in pending.descriptions:
        raise Refusal(f"the answer for {pending.nonce} is an option description, not a choice")
    return found


def _tier_data(answer: str) -> dict:
    tier = tiers.chosen(answer)
    return {"level": tier.level, "fact": tier.fact}


def _owner_signs(store: Store, pending: Pending, by: str) -> None:
    story = store.issue(pending.stories[0])
    ref = story["identifier"]
    if story["assignee"] is None:
        raise Refusal(f"{ref} has no owner in Linear, and only its owner signs it off")
    if story["assignee"] != by:
        raise Refusal(f"only {story['assignee_name']}, who owns {ref}, signs it off")


def _another_signs(store: Store, pending: Pending, by: str) -> None:
    story = store.issue(pending.stories[0])
    if story["assignee"] == by:
        raise Refusal(
            f"{story['identifier']} belongs to {story['assignee_name']}; a second sign-off comes from someone else"
        )


def decide(store: Store, pending: Pending, state: Path, by: str) -> bool:
    found = check(pending, state)
    if not _creates_record(pending, found.answer):
        return False
    if pending.kind == signoff.KIND:
        _owner_signs(store, pending, by)
    if pending.kind == signoff.SECOND:
        _another_signs(store, pending, by)
    graded = {}
    if pending.kind == quiz.KIND:
        records = store.records(pending.stories[0])
        graded = {"correct": quiz.grade(records, pending.subject, pending.version, pending.options, found.answer)}
    record = Record(
        pending.kind,
        pending.subject,
        pending.version,
        f"person:{found.session_id}",
        {"answer": found.answer, "question": pending.question, "nonce": pending.nonce, "by": by}
        | ({"item": pending.detail} if pending.kind == "decision-item" else {})
        | (_tier_data(found.answer) if pending.kind == "tier" else {})
        | graded,
    )
    for story in pending.stories:
        ops.ensure_record(store, story, record)
    return True
