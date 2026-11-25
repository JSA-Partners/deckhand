from __future__ import annotations

from collections.abc import Mapping

from deckhand import fleet, wakes
from deckhand.records import epoch
from deckhand.store.state import HandRow, Message

IDLE_AFTER = 120.0


# Deckhand's own turn end outranks the fleet's state word, which can read working long after a turn ends.
def _turn_over(row: HandRow, session: fleet.Session, heard: Mapping[str, str]) -> bool:
    if not row.turn_ended:
        return False
    since = (at for at in (session.started_at, heard.get(row.name)) if at)
    return all(epoch(row.turn_ended) > epoch(at) for at in since)


# A hand's own nudge does not end its idleness, so it still reads as idle while the nudge goes unheard.
def idle(
    read: fleet.Fleet,
    hands: Mapping[str, HandRow],
    pending: list[Message],
    now: float,
    woke: float,
    heard: Mapping[str, str],
) -> frozenset[str]:
    if read.note:
        return frozenset()
    mailed = {m.hand for m in pending if m.kind != wakes.NUDGE}
    found = set()
    for name, row in hands.items():
        alive = [s for s in read.sessions if s.name == name and fleet.alive(s)]
        session = max(alive, key=lambda s: s.started_at, default=None)
        if session is None or not fleet.live(session) or session.waiting_for or name in mailed:
            continue
        if _turn_over(row, session, heard) and now - max(epoch(row.turn_ended), woke) > IDLE_AFTER:
            found.add(name)
    return frozenset(found)


# A nudge the hand has not heard yet still has its unheard window to run before anyone asks the person.
def nudged(pending: list[Message], now: float, woke: float) -> frozenset[str]:
    return frozenset(
        m.hand
        for m in pending
        if m.kind == wakes.NUDGE and m.delivered is None and now - max(epoch(m.written), woke) <= IDLE_AFTER
    )
