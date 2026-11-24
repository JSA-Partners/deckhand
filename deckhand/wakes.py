from __future__ import annotations

from deckhand import fleet, queue, review
from deckhand.records import Record
from deckhand.store.state import Message

NUDGE, SYNC = "nudge", "sync"
ACK = "Then acknowledge with: deckhand mail apply {ref} ack {seq}"


def to_fix(records: list[Record]) -> list[str]:
    return [f"{queue.finding_line(records, f['id'])[1]} Fix: {f['fix']}" for f in review.unfixed(records)]


def unfixed_lines(records: list[Record]) -> list[str]:
    return [f"accepted, not yet fixed: {line}" for line in to_fix(records)]


def body(story: dict, decision: Record, records: list[Record]) -> str:
    lines = [f"The person decided {decision.kind}: {decision.data.get('answer', '')}"]
    if decision.data.get("note"):
        lines.append(f"Reason: {decision.data['note']}")
    if decision.kind == review.DECISION:
        lines.append(queue.finding_line(records, decision.subject)[1])
        found = review.locate(records, decision.subject)
        if found is not None:
            lines.append(f"Fix: {found[1]['fix']}")
    left = to_fix(records)
    if left:
        lines += ["Accepted findings still to fix:", *(f"- {line}" for line in left)]
    if len(lines) == 1:
        lines.append(nudge_body())
    return "\n".join(lines)


def nudge_body() -> str:
    return "Carry on from the first step the contexts show undone."


def sync_body(merged: dict) -> str:
    return f"{merged['identifier']} merged. Bring main in before your next commit."


def prompt(messages: list[Message]) -> str:
    ordered = sorted(messages, key=lambda m: m.seq)
    last = ordered[-1]
    ack = ACK.format(ref=fleet.ref_of(last.hand), seq=last.seq)
    return "\n\n".join([*(m.body for m in ordered), ack])
