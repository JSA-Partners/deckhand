from __future__ import annotations

from dataclasses import dataclass

from deckhand import asks, checks, features, implplan, order, queue, review, signoff, versions
from deckhand.records import Record

DRAFTING, PLANNING, PLAN_REVIEW, BUILDING = "drafting", "planning", "plan review", "building"
CHECKS, BRANCH_REVIEW, SIGN_OFF, MERGING, MERGED = "checks", "branch review", "sign-off", "merging", "merged"
_PLANNED = (queue.DRAFT, queue.TAKEN, features.APPROVAL)


@dataclass(frozen=True)
class Stage:
    name: str
    round: int | None
    since: str | None


def as_data(stage: Stage) -> dict:
    return {"name": stage.name, "round": stage.round, "since": stage.since}


def _newest(records: list[Record]) -> str | None:
    return max((r.at for r in records), default=None)


def _rounds(records: list[Record], story: str, stage: str) -> list[Record]:
    held, _ = review.counted(records, f"{story}:{stage}")
    return held


def _review(name: str, held: list[Record]) -> Stage | None:
    latest = review.last(held)
    return Stage(name, review.round_number(latest), latest.at) if latest is not None else None


def _signing(records: list[Record], story: str, head: str) -> Stage | None:
    sign = signoff.signed(records, story)
    if (
        sign is not None
        and sign.data.get("answer") == signoff.SIGN
        and signoff.signed_head(sign) == head
        and signoff.voided(records, story, sign) is None
    ):
        return Stage(MERGING, None, sign.at)
    summary = versions.held(records, signoff.SUMMARY, story)
    if summary is not None and summary.data.get("head") == head:
        return Stage(SIGN_OFF, None, summary.at)
    return None


def _building(records: list[Record], story: str) -> Stage | None:
    plan = versions.held(records, implplan.PURPOSE, story)
    if plan is None:
        return None
    held = _rounds(records, story, "plan")
    if review.cleared(records, story, "plan", plan.version):
        answers = review.cap_answers(records, f"{story}:plan")
        return Stage(BUILDING, None, _newest([*held, *answers]))
    return _review(PLAN_REVIEW, held)


def _planning(records: list[Record]) -> Stage:
    starts = [r for r in records if r.kind == asks.HAND_START]
    story_starts = [r for r in starts if r.data.get("kind") == queue.STORY_HAND]
    planned = [r for r in records if r.kind in _PLANNED]
    if story_starts or planned:
        return Stage(PLANNING, None, _newest(story_starts or planned))
    drafts = [r for r in starts if r.data.get("kind") == queue.DRAFT_HAND]
    return Stage(DRAFTING, None, _newest(drafts))


def of(story: dict, records: list[Record]) -> Stage:
    id = story["id"]
    if story["state"] == order.DONE:
        return Stage(MERGED, None, None)
    checked = versions.held(records, checks.KIND, id)
    signing = _signing(records, id, checked.version) if checked is not None else None
    if signing is not None:
        return signing
    branch = _review(BRANCH_REVIEW, _rounds(records, id, "branch"))
    if branch is not None:
        return branch
    if checked is not None:
        return Stage(CHECKS, None, checked.at)
    return _building(records, id) or _planning(records)
