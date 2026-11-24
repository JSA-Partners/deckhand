from __future__ import annotations

import json
from dataclasses import asdict

from deckhand import features, implplan, quiz, review, versions
from deckhand.checks import passing
from deckhand.records import HAND, Record, content_hash
from deckhand.step import Refusal
from deckhand.store import Store
from deckhand.summary import ChangeSummary, digest, parse_summary

KIND = "sign-off"
SIGN, NOT_YET, DONE, BACK = "Sign off", "Not yet", "Done", "Back"
SUMMARY = "change-summary"
VOID = "sign-off-void"
TUICR = "tuicr-review"
SECOND = "second-sign-off"
AFTER = "after-merge"
AFTER_DONE = "after-merge-done"
AFTER_PERSON = "after-merge-person"
OPEN, BY_HAND, FOR_PERSON, BY_PERSON = "open", "done by the hand", "left for the person", "done by the person"


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


def _plan_gap(records: list[Record], story: str) -> list[str]:
    plan = versions.held(records, implplan.PURPOSE, story)
    if plan is None or not review.cleared(records, story, "plan", plan.version):
        return ["the plan review has not stopped clean at the current plan version"]
    return []


def _tuicr_gap(records: list[Record], story: str, head: str) -> list[str]:
    held = versions.held(records, TUICR, story)
    if held is None or held.data.get("head") != head:
        return [f"no tuicr review is built at {head}; run signoff apply REF review"]
    return []


def gaps(records: list[Record], story: str, head: str) -> list[str]:
    summary = held_summary(records, story, head)
    found = [] if summary else [f"no change summary is saved at {head}"]
    found += _quiz_gap(records, story, head, summary)
    for stage in ("plan", "branch"):
        found += _plan_gap(records, story) if stage == "plan" else _branch_gap(records, story, head)
        found += review.delivery_gaps(records, story, stage) + review.left_open_gaps(records, story, stage)
    found += _tuicr_gap(records, story, head)
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


def void_record(story: str, sign: Record, reason: str, author: str = "agent:merge") -> Record:
    nonce, head = sign.data.get("nonce", ""), signed_head(sign)
    return Record(VOID, f"{story}:{nonce}", head, author, {"head": head, "nonce": nonce, "reason": reason})


def voided(records: list[Record], story: str, sign: Record) -> Record | None:
    return versions.held(records, VOID, f"{story}:{sign.data.get('nonce', '')}")


def _first(records: list[Record], story: str, head: str) -> bool:
    held = signed(records, story)
    return (
        held is not None
        and held.data.get("answer") == SIGN
        and held.version == version(records, story, head)
        and not gaps(records, story, head)
        and voided(records, story, held) is None
    )


def second_version(sign: Record) -> str:
    return f"{sign.version}:{sign.data.get('nonce', '')}"


def seconded(records: list[Record], story: str, sign: Record) -> bool:
    held = versions.held(records, SECOND, story)
    return (
        held is not None
        and held.author.startswith(review.PERSON)
        and held.data.get("answer") == SIGN
        and held.version == second_version(sign)
        and held.data.get("by") != sign.data.get("by")
        and voided(records, story, sign) is None
    )


def current(records: list[Record], story: str, head: str, second: bool = False) -> bool:
    if not _first(records, story, head):
        return False
    held = signed(records, story)
    return not second or (held is not None and seconded(records, story, held))


def standing(records: list[Record], story: str, head: str, second: bool = False) -> str | None:
    if current(records, story, head, second):
        return None
    if _first(records, story, head):
        return "waiting for a second sign-off"
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
        return f"the head moved to {head}"
    return "the sign-off no longer matches the change summary or the quiz"


def after_items(records: list[Record], story: str) -> list[str]:
    plan = versions.held(records, implplan.PURPOSE, story)
    return [str(text) for text in plan.data.get("after_merge", [])] if plan else []


def after_record(kind: str, story: str, n: int, text: str, data: dict) -> Record:
    return Record(kind, f"{story}:{n}", content_hash(text), HAND, {"n": n, "text": text} | data)


def after_status(records: list[Record], story: str, n: int, text: str) -> tuple[str, Record | None]:
    subject, at = f"{story}:{n}", content_hash(text)
    done = versions.held(records, AFTER_DONE, subject)
    if done is not None and done.version == at:
        return BY_HAND, done
    left = versions.held(records, AFTER_PERSON, subject)
    if left is None or left.version != at:
        return OPEN, None
    decided = versions.held(records, AFTER, subject)
    if (
        decided is not None
        and decided.author.startswith(review.PERSON)
        and decided.version == left.version
        and decided.data.get("answer") == DONE
    ):
        return BY_PERSON, decided
    return FOR_PERSON, left


def _left(records: list[Record], story: str) -> list[tuple[int, str, Record]]:
    found = []
    for n, text in enumerate(after_items(records, story), start=1):
        status, held = after_status(records, story, n, text)
        if status == FOR_PERSON and held is not None:
            found.append((n, text, held))
    return found


def after_merge_for_person(records: list[Record], story: str) -> list[tuple[int, str, str]]:
    return [(n, text, str(held.data.get("why", ""))) for n, text, held in _left(records, story)]


def _left_elsewhere(store: Store, seen: set[str]) -> bool:
    return any(_left(store.records(issue), issue) for issue in store.recorded(AFTER_PERSON) if issue not in seen)


def after_merge_queue(store: Store, team: str, me: str) -> list[tuple[dict, int, str, str, str]]:
    held = features.open_features(store, team)
    if _left_elsewhere(store, {s["id"] for feature in held for s in feature.stories}):
        held += features.released(store, team)
    found = []
    for feature in held:
        for story in feature.stories:
            if story["assignee"] == me:
                held = _left(feature.records.get(story["id"], []), story["id"])
                found += [(story, n, text, str(left.data.get("why", "")), left.at) for n, text, left in held]
    return found
