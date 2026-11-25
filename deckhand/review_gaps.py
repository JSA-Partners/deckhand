from __future__ import annotations

from dataclasses import dataclass

from deckhand.records import Record
from deckhand.review import (
    PROPOSAL,
    accepted,
    at_cap,
    counted,
    decided,
    excused,
    last,
    open_p1,
    proceeded,
    reviewed,
    rounds_of,
    waived,
)


@dataclass(frozen=True)
class Gap:
    id: str
    hand: str
    person: str


def both(id: str, text: str) -> Gap:
    return Gap(id, text, text)


def undecided_delivery(records: list[Record], story: str, stage: str) -> list[dict]:
    held, _ = counted(records, f"{story}:{stage}")
    settled = set(decided(records)) | waived(records, f"{story}:{stage}")
    found = {f["id"]: f for r in held for f in r.data["findings"] if f.get("changes_delivery")}
    return [f for id, f in found.items() if id not in settled]


def delivery_line(item: dict) -> str:
    return f"touches: {item['touches']}" if item.get("touches") else "it changes what the story delivers"


def delivery_gaps(records: list[Record], story: str, stage: str, ref: str) -> list[Gap]:
    proposed = {r.subject for r in records if r.kind == PROPOSAL}
    found = []
    for id in (f["id"] for f in undecided_delivery(records, story, stage)):
        if id in proposed:
            hand = f"{id} waits for the person's decision"
        else:
            hand = (
                f"{id} changes what the story delivers; propose an answer with review apply {ref} resolve"
                f" --stage {stage} --propose-accept {id} or --propose-decline {id} --reason TEXT"
            )
        person = (
            f"finding {id} changes what the story delivers; decide it"
            if id in proposed
            else f"finding {id} waits for the hand"
        )
        found.append(Gap(id, hand, person))
    return found


def left_open(records: list[Record], story: str, stage: str) -> list[dict]:
    held, _ = counted(records, f"{story}:{stage}")
    settled = accepted(records) | set(decided(records))
    found = {f["id"]: f for r in held for f in r.data["findings"]}
    return [
        f for id, f in found.items() if f["severity"] != "P1" and not f.get("changes_delivery") and id not in settled
    ]


def left_open_gaps(records: list[Record], story: str, stage: str, ref: str) -> list[Gap]:
    proposed = {r.subject for r in records if r.kind == PROPOSAL}
    found = []
    for f in left_open(records, story, stage):
        id = f["id"]
        if id in proposed:
            hand = f"{id} waits for the person's decision"
        else:
            hand = (
                f"{id} is a {f['severity']} left open; resolve it with review apply {ref} resolve --stage {stage}"
                f" --accept {id}, or --propose-decline {id} --reason TEXT"
            )
        person = (
            f"finding {id} was left open by the hand; decide it"
            if id in proposed
            else f"finding {id} waits for the hand"
        )
        found.append(Gap(id, hand, person))
    return found


def _feature_delivery(records: list[Record], project: str, ref: str) -> list[Gap]:
    proposed = {r.subject for r in records if r.kind == PROPOSAL}
    found = []
    for id in (f["id"] for f in undecided_delivery(records, project, "feature")):
        if id in proposed:
            hand = f"{id} waits for the person: decide context finding {ref} --finding {id}"
            person = f"finding {id} changes what the feature delivers; decide it"
        else:
            hand = (
                f"{id} changes what the feature delivers; propose an answer with feature-review apply resolve"
                f" --propose-accept {id} or --propose-decline {id} --reason TEXT"
            )
            person = f"finding {id} changes what the feature delivers; it waits for a proposal"
        found.append(Gap(id, hand, person))
    return found


def _feature_p1(records: list[Record], prefix: str, latest: Record, ref: str) -> list[Gap]:
    held, excusing = counted(records, prefix)[0], excused(records, prefix)
    capped = at_cap(held, excusing, proceeded(records, prefix))
    settled = set(decided(records))
    proposed = {r.subject for r in records if r.kind == PROPOSAL} - settled
    found = []
    for f in open_p1(latest, excusing):
        id = f["id"]
        person = f"the feature review holds an open P1 {id}: {f['summary']}"
        if id in proposed:
            hand = f"{person}; it waits for the person: decide context finding {ref} --finding {id}"
        elif capped:
            hand = f"{person}; three rounds left a P1 open: decide context review-cap {ref} --stage feature"
        elif id in settled:
            hand = f"{person}; fix it and review again"
        else:
            hand = (
                f"{person}; fix it and review again, or propose declining it with feature-review apply resolve"
                f" --propose-decline {id} --reason TEXT"
            )
        found.append(Gap(id, hand, person))
    return found


def feature_gaps(project: str, records: list[Record], at: str, ref: str) -> list[Gap]:
    prefix = f"{project}:feature"
    latest = last(rounds_of(records, prefix))
    if latest is None:
        return [both("feature-review", "the feature review has not run")]
    if reviewed(latest) != at:
        text = "the feature review has not reviewed the current contract and stories; run a feature round"
        return [both("feature-review", text)]
    p1 = _feature_p1(records, prefix, latest, ref)
    held = {gap.id for gap in p1}
    return p1 + [gap for gap in _feature_delivery(records, project, ref) if gap.id not in held]
