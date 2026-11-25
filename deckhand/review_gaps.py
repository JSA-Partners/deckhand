from __future__ import annotations

from dataclasses import dataclass

from deckhand.records import Record
from deckhand.review import PROPOSAL, accepted, counted, decided, declined, last, open_p1, reviewed, rounds_of, waived


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


def feature_gaps(project: str, records: list[Record], at: str, ref: str) -> list[Gap]:
    rounds = rounds_of(records, f"{project}:feature")
    latest = last(rounds)
    if latest is None:
        return [both("feature-review", "the feature review has not run")]
    if reviewed(latest) != at:
        text = "the feature review has not reviewed the current contract and stories; run a feature round"
        return [both("feature-review", text)]
    found = open_p1(latest, declined(records))
    return [both(f["id"], f"the feature review holds an open P1 {f['id']}: {f['summary']}") for f in found]
