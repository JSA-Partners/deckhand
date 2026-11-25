from __future__ import annotations

import json
from dataclasses import asdict

from deckhand import implplan, review, review_gaps, tuicr, versions
from deckhand.checks import passing
from deckhand.records import Record
from deckhand.review_gaps import Gap, both
from deckhand.step import Refusal, hand_role
from deckhand.summary import ChangeSummary, digest, parse_summary

KIND = "sign-off"
SIGN, NOT_YET, DONE, BACK, LATER = "Sign off", "Not yet", "Done", "Back", "Later"
SUMMARY = "change-summary"
VOID = "sign-off-void"
TUICR = "tuicr-review"
IN_TUICR = "tuicr"
SECOND = "second-sign-off"


def summary_record(story: str, head: str, summary: ChangeSummary) -> Record:
    found = digest(summary)
    return Record(SUMMARY, story, found, "agent:signoff", {"summary": asdict(summary), "digest": found, "head": head})


# A diff that only deletes, renames or changes modes shows no new line, so it has nothing to check.
def check_to_check(summary: ChangeSummary, hunks: list[tuicr.Hunk]) -> None:
    shown = tuicr.describe_ranges(tuicr.shown_ranges(hunks))
    if shown and not summary.to_check:
        raise Refusal("summary field to_check must hold at least one entry, the riskiest line of the diff")
    for index, entry in enumerate(summary.to_check):
        if not tuicr.holds(hunks, entry.where):
            where = f"to_check entry {index} names {entry.where}"
            raise Refusal(f"{where}, which the diff does not show; it shows {shown or 'no new line'}")


def held_summary(records: list[Record], story: str, head: str) -> ChangeSummary | None:
    held = versions.held(records, SUMMARY, story)
    if held is None or held.data.get("head") != head:
        return None
    try:
        return parse_summary(json.dumps(held.data.get("summary")))
    except Refusal:
        return None


def _branch_gap(records: list[Record], story: str, head: str) -> list[Gap]:
    if review.cleared(records, story, "branch", head):
        return []
    latest = review.last(review.counted(records, f"{story}:branch")[0])
    if latest is None or review.reviewed(latest) != head:
        return [
            Gap(
                "branch-review",
                f"the branch review has not reviewed the signed head {head}; run a branch round",
                f"the branch review has not reviewed the signed head {head}",
            )
        ]
    return [
        Gap(
            "branch-review",
            "the branch review has not stopped clean, a P1 is open; fix it; at the cap the person decides",
            "the branch review has not stopped clean, a P1 is open",
        )
    ]


def _plan_gap(records: list[Record], story: str) -> list[Gap]:
    plan = versions.held(records, implplan.PURPOSE, story)
    if plan is None or not review.cleared(records, story, "plan", plan.version):
        return [both("plan-review", "the plan review has not stopped clean at the current plan version")]
    return []


def _tuicr_gap(records: list[Record], story: str, head: str) -> list[Gap]:
    held = versions.held(records, TUICR, story)
    summary = held_summary(records, story, head)
    stale = summary is not None and held is not None and held.data.get("summary") != digest(summary)
    if held is None or held.data.get("head") != head or stale:
        text = f"no tuicr review is built at {head}"
        return [Gap("tuicr", f"{text}; it waits for the person's tuicr review", text)]
    return []


def done_in_tuicr(records: list[Record], story: str, head: str | None) -> bool:
    held = versions.held(records, IN_TUICR, story)
    return held is not None and held.version == head and held.author.startswith(review.PERSON)


def gaps(records: list[Record], story: str, head: str, ref: str) -> list[Gap]:
    summary = held_summary(records, story, head)
    found = [] if summary else [both("summary", f"no change summary is saved at {head}")]
    for stage in ("plan", "branch"):
        found += _plan_gap(records, story) if stage == "plan" else _branch_gap(records, story, head)
        found += review_gaps.delivery_gaps(records, story, stage, ref)
        found += review_gaps.left_open_gaps(records, story, stage, ref)
    found += _tuicr_gap(records, story, head)
    if not passing(records, story, head):
        found.append(both("checks", f"checks are not passing at {head}"))
    return found


def version(head: str, summary: ChangeSummary) -> str:
    return f"{head}:{digest(summary)}"


def version_at(records: list[Record], story: str, head: str) -> str | None:
    summary = held_summary(records, story, head)
    return version(head, summary) if summary is not None else None


def signed(records: list[Record], story: str) -> Record | None:
    held = versions.held(records, KIND, story)
    return held if held is not None and held.author.startswith(review.PERSON) else None


def signed_head(record: Record) -> str:
    return record.version.split(":", 1)[0]


def void_record(story: str, sign: Record, reason: str, author: str = "agent:merge") -> Record:
    nonce, head = sign.data.get("nonce", ""), signed_head(sign)
    shown = {key: sign.data[key] for key in ("name", "repo") if key in sign.data}
    data = {"head": head, "nonce": nonce, "reason": reason, "signed_at": sign.at} | shown
    return Record(VOID, f"{story}:{nonce}", head, author, data)


def voided(records: list[Record], story: str, sign: Record) -> Record | None:
    return versions.held(records, VOID, f"{story}:{sign.data.get('nonce', '')}")


def _first(records: list[Record], story: str, head: str) -> bool:
    held = signed(records, story)
    return (
        held is not None
        and held.data.get("answer") == SIGN
        and held.version == version_at(records, story, head)
        and not gaps(records, story, head, story)
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
    unreviewed = _plan_gap(records, story) + _branch_gap(records, story, head)
    if unreviewed and held.version == version_at(records, story, head):
        return unreviewed[0].hand if hand_role() else unreviewed[0].person
    return "the sign-off no longer matches the change summary"
