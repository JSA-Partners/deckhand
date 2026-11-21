from __future__ import annotations

import json
import posixpath
from pathlib import PurePosixPath

from deckhand import review, signoff
from deckhand.questions import MAX_OPTIONS, MIN_OPTIONS, RECOMMENDED
from deckhand.records import HAND, Record, content_hash
from deckhand.step import Refusal

QUESTION = "hand-question"
ANSWER = "question"
EDIT = "config-edit"
EDIT_REQUEST = "config-edit-request"
PROPOSAL = "decline-proposal"
HAND_START = "hand-start"
WAKE = "wake"
EDIT_OPTIONS = ["Done", "Not doing it"]
QUESTION_SHAPE = {"question": "TEXT", "options": ["TWO TO FOUR OPTIONS"], "why": "TEXT"}
EDIT_SHAPE = {"path": "PATH", "change": "TEXT", "why": "TEXT"}
WAITING = (ANSWER, review.DECISION, review.CAP_DECISION, signoff.KIND, signoff.SECOND, EDIT)
_OWN_FILES = ("deckhand.toml", "deckhand")


def _object(text: str, shape: dict) -> dict:
    try:
        found = json.loads(text)
    except ValueError:
        raise Refusal("the file is not valid JSON") from None
    if not isinstance(found, dict) or set(found) != set(shape):
        raise Refusal(f"the file must be a JSON object with exactly {', '.join(shape)}")
    for name in shape:
        if name != "options" and (not isinstance(found[name], str) or not found[name].strip()):
            raise Refusal(f"{name} must be a non-empty string")
    return found


def question_id(question: str) -> str:
    return content_hash(question)[:12]


def parse_question(text: str) -> dict:
    found = _object(text, QUESTION_SHAPE)
    options = found["options"]
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise Refusal("options must be a list of strings")
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise Refusal(f"a question takes {MIN_OPTIONS} to {MAX_OPTIONS} options, not {len(options)}")
    if any(not o.strip() for o in options):
        raise Refusal("a question takes no empty option")
    keys = [o.strip().casefold() for o in options]
    if len(set(keys)) != len(keys):
        raise Refusal("a question takes no duplicate option")
    if any(RECOMMENDED.search(text) for text in [found["question"], found["why"], *options]):
        raise Refusal("a question names no recommended option; the person chooses without a lean")
    return found


def question_record(story: str, found: dict) -> Record:
    version = content_hash(json.dumps(found, sort_keys=True))
    return Record(QUESTION, f"{story}:{question_id(found['question'])}", version, HAND, found)


def parse_edit(text: str) -> dict:
    found = _object(text, EDIT_SHAPE)
    parts = PurePosixPath(posixpath.normpath(found["path"])).parts
    for at, part in enumerate(parts[:-1]):
        if part == ".claude" and parts[at + 1] in _OWN_FILES:
            raise Refusal(f"{found['path']} is deckhand's own; a hand edits those files itself")
    return found


def edit_id(found: dict) -> str:
    return content_hash(json.dumps(found, sort_keys=True))[:12]


def edit_record(story: str, found: dict) -> Record:
    return Record(EDIT_REQUEST, f"{story}:{edit_id(found)}", edit_id(found), HAND, found)


def proposal_record(finding: str, version: str, reason: str) -> Record:
    return Record(PROPOSAL, finding, version, "agent:review", {"reason": reason})


def open_proposals(records: list[Record]) -> list[Record]:
    settled = review.decided(records)
    return [r for r in records if r.kind == PROPOSAL and r.subject not in settled]


def _person(record: Record) -> bool:
    return record.author.startswith(review.PERSON)


def answer_of(records: list[Record], subject: str, kind: str) -> Record | None:
    found = [r for r in records if r.kind == kind and r.subject == subject]
    return found[-1] if found else None


def _finding_waited(records: list[Record], finding: str, story: str) -> bool:
    found = review.locate(records, finding)
    if found is None or not found[0].subject.startswith(f"{story}:"):
        return False
    return found[1].get("changes_delivery") is True or any(r.kind == PROPOSAL and r.subject == finding for r in records)


def _waited(records: list[Record], decision: Record, story: str) -> bool:
    asked = {ANSWER: QUESTION, EDIT: EDIT_REQUEST}.get(decision.kind)
    if asked is not None:
        return any(r.kind == asked and r.subject == f"{story}:{decision.subject}" for r in records)
    if decision.kind == review.DECISION:
        return _finding_waited(records, decision.subject, story)
    if decision.kind == review.CAP_DECISION:
        return decision.subject in (review.cap_subject(story, "plan"), review.cap_subject(story, "branch"))
    return decision.subject == story


# A decision made before the merge was for the build the merge finished, so it is never due after.
def wake_due(records: list[Record], story: str, merged_at: str = "") -> Record | None:
    starts = [r for r in records if r.kind == HAND_START]
    if not starts:
        return None
    floor = max([merged_at, *(r.at for r in records if not _person(r))])
    due = [r for r in records if _person(r) and r.kind in WAITING and r.at > floor and _waited(records, r, story)]
    return max(due, key=lambda r: r.at, default=None)
