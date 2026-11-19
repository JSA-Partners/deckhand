from __future__ import annotations

import json
from dataclasses import asdict

from deckhand import quiz, review, versions
from deckhand.checks import passing
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.summary import ChangeSummary, digest, parse_summary

KIND = "sign-off"
SIGN, NOT_YET = "Sign off", "Not yet"
SUMMARY = "change-summary"
VOID = "sign-off-void"
TUICR = "tuicr-review"


def summary_record(story: str, head: str, summary: ChangeSummary) -> Record:
    found = digest(summary)
    return Record(SUMMARY, story, found, "agent:signoff", {"summary": asdict(summary), "digest": found, "head": head})


def held_summary(records: list[Record], story: str, head: str) -> ChangeSummary | None:
    held = versions.held(records, SUMMARY, story)
    if held is None or held.data.get("head") != head:
        return None
    try:
        return parse_summary(json.dumps(held.data.get("summary")))
    except Refusal:
        return None


def _quiz_gap(records: list[Record], story: str, head: str, summary: ChangeSummary | None) -> list[str]:
    held = quiz.held_set(records, story)
    if held is None or held.data.get("head") != head:
        return [f"no quiz is saved at {head}; a story with nothing to ask still saves an empty quiz"]
    found = []
    if summary is not None and held.data.get("summary") != digest(summary):
        found.append("the change summary differs from the one the quiz was written from; save the quiz again")
    if not quiz.passed(records, story, head):
        found.append("quiz items still open: " + ", ".join(str(n) for n in quiz.open_items(records, story)))
    return found


def _branch_gap(records: list[Record], story: str, head: str) -> list[str]:
    if review.cleared(records, story, "branch", head):
        return []
    latest = review.last(review.counted(records, f"{story}:branch")[0])
    if latest is not None and review.reviewed(latest) != head:
        return [f"the branch review has not reviewed the signed head {head}; run a branch round"]
    return ["the branch review has not stopped clean, and the person has not decided Proceed at its cap"]


def gaps(records: list[Record], story: str, head: str) -> list[str]:
    summary = held_summary(records, story, head)
    found = [] if summary else [f"no change summary is saved at {head}"]
    found += _quiz_gap(records, story, head, summary)
    found += _branch_gap(records, story, head)
    if not passing(records, story, head):
        found.append(f"checks are not passing at {head}")
    return found


def version(records: list[Record], story: str, head: str) -> str | None:
    summary = held_summary(records, story, head)
    held = quiz.held_set(records, story)
    if summary is None or held is None:
        return None
    return f"{head}:{digest(summary)}:{held.version}"


def signed(records: list[Record], story: str) -> Record | None:
    held = versions.held(records, KIND, story)
    return held if held is not None and held.author.startswith(review.PERSON) else None


def signed_head(record: Record) -> str:
    return record.version.split(":", 1)[0]


def void_record(story: str, sign: Record, reason: str) -> Record:
    nonce, head = sign.data.get("nonce", ""), signed_head(sign)
    return Record(VOID, f"{story}:{nonce}", head, "agent:merge", {"head": head, "nonce": nonce, "reason": reason})


def voided(records: list[Record], story: str, sign: Record) -> Record | None:
    return versions.held(records, VOID, f"{story}:{sign.data.get('nonce', '')}")


def current(records: list[Record], story: str, head: str) -> bool:
    held = signed(records, story)
    return (
        held is not None
        and held.data.get("answer") == SIGN
        and held.version == version(records, story, head)
        and not gaps(records, story, head)
        and voided(records, story, held) is None
    )


def standing(records: list[Record], story: str, head: str) -> str | None:
    if current(records, story, head):
        return None
    held = signed(records, story)
    if held is None:
        return "the person has not signed off"
    if held.data.get("answer") != SIGN:
        return f"the person answered {held.data.get('answer')}"
    at = signed_head(held)
    void = voided(records, story, held)
    if void is not None:
        return f"the sign-off at {at} is void: {void.data.get('reason')}"
    if at != head:
        return f"the sign-off is for {at}, not the head {head}"
    return "the sign-off no longer matches the change summary or the quiz"
