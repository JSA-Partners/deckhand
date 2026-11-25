from __future__ import annotations

from deckhand import asks, chores, fleet, queue
from deckhand.records import Record

STALLED = "stalled"
RESTART, LEAVE = "Restart", "Leave it stopped"


def _waiting(story: dict, records: list[Record], second: bool) -> bool:
    asked = queue._for_person(story, records, 2) or asks.open_proposals(records)
    if second or asked or chores.after_merge_for_person(records, story["id"]):
        return True
    return asks.wake_due(records, story["id"], queue.merged_at(story)) is not None


def stalled(
    story: dict,
    records: list[Record],
    session: fleet.Session | None,
    launch: str,
    second: bool = False,
    idle: bool = False,
) -> bool:
    start = queue.last_start(records, launch)
    if start is None or (session is not None and fleet.alive(session) and not idle):
        return False
    return not _waiting(story, records, second) and not chores.finished(story, records, start)


def _new_since_start(records: list[Record], launch: str) -> bool:
    start = queue.last_start(records, launch)
    quiet = (asks.HAND_START, STALLED)
    return start is not None and any(r.at > start.at and r.kind not in quiet for r in records)


def auto_restart(records: list[Record], launch: str, restarts: int | None) -> bool:
    return restarts == 0 or _new_since_start(records, launch)


def stall_answer(records: list[Record], launch: str) -> str | None:
    start = queue.last_start(records, launch)
    if start is None:
        return None
    found = [r for r in records if r.kind == STALLED and r.version == start.at]
    return str(found[-1].data.get("answer")) if found else None


def items(
    story: dict,
    records: list[Record],
    session: fleet.Session | None,
    rank: int,
    launch: str,
    second: bool,
    restarts: int | None,
    idle: bool = False,
) -> list[queue.Item]:
    if not stalled(story, records, session, launch, second, idle) or auto_restart(records, launch, restarts):
        return []
    start = queue.last_start(records, launch)
    if start is None or stall_answer(records, launch) is not None:
        return []
    state = "idle" if idle else session.state if session is not None else "gone"
    lines = [f"its hand is {state}, and no record is new since it last started"]
    next = [f"decide context {STALLED} {story['identifier']}"]
    return [queue._story_item(STALLED, rank, story, start.at, lines, next)]
