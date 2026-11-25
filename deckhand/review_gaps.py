from __future__ import annotations

from deckhand.records import Record
from deckhand.review import PROPOSAL, accepted, counted, decided, declined, last, open_p1, reviewed, rounds_of, waived


def undecided_delivery(records: list[Record], story: str, stage: str) -> list[dict]:
    held, _ = counted(records, f"{story}:{stage}")
    settled = set(decided(records)) | waived(records, f"{story}:{stage}")
    found = {f["id"]: f for r in held for f in r.data["findings"] if f.get("changes_delivery")}
    return [f for id, f in found.items() if id not in settled]


def delivery_line(item: dict) -> str:
    return f"touches: {item['touches']}" if item.get("touches") else "it changes what the story delivers"


def delivery_gaps(records: list[Record], story: str, stage: str, ref: str = "REF") -> list[str]:
    proposed = {r.subject for r in records if r.kind == PROPOSAL}
    return [
        f"{id} waits for the person's decision"
        if id in proposed
        else f"{id} changes what the story delivers; propose an answer with review apply {ref} resolve --stage {stage}"
        f" --propose-accept {id} or --propose-decline {id} --reason TEXT"
        for id in (f["id"] for f in undecided_delivery(records, story, stage))
    ]


def left_open(records: list[Record], story: str, stage: str) -> list[dict]:
    held, _ = counted(records, f"{story}:{stage}")
    settled = accepted(records) | set(decided(records))
    found = {f["id"]: f for r in held for f in r.data["findings"]}
    return [
        f for id, f in found.items() if f["severity"] != "P1" and not f.get("changes_delivery") and id not in settled
    ]


def left_open_gaps(records: list[Record], story: str, stage: str, ref: str = "REF") -> list[str]:
    return [
        f"{f['id']} is a {f['severity']} the hand left open and the person has not decided it:"
        f" decide context finding {ref} --finding {f['id']}"
        for f in left_open(records, story, stage)
    ]


def feature_gaps(project: str, records: list[Record], at: str) -> list[str]:
    rounds = rounds_of(records, f"{project}:feature")
    latest = last(rounds)
    if latest is None:
        return ["the feature review has not run"]
    if reviewed(latest) != at:
        return ["the feature review has not reviewed the current contract and stories; run a feature round"]
    found = open_p1(latest, declined(records))
    return [f"the feature review holds an open P1 {f['id']}: {f['summary']}" for f in found]
