from __future__ import annotations

from dataclasses import dataclass, replace

from deckhand.store import state

QUEUE = "deckhand: something needs the person"
STOPPED = "deckhand: the engine stopped"
RUNNING = "deckhand: the engine is running"
MAIL = "deckhand: a message waits"
HOLD = "hold"


@dataclass(frozen=True)
class Seen:
    top: str
    engine: bool | None
    seq: int
    at: float


def captain(seen: Seen, top: str, held: bool, beat: float | None, now: float) -> tuple[Seen, list[str]]:
    if now - seen.at > state.SLEEP_GAP:
        return replace(seen, at=now), []
    lines = []
    shown = seen.top
    if not top:
        shown = ""
    elif top != seen.top and not held:
        lines.append(QUEUE)
        shown = top
    engine = state.alive(beat, now)
    if seen.engine is not None and engine != seen.engine:
        lines.append(RUNNING if engine else STOPPED)
    return Seen(shown, engine, seen.seq, now), lines


def hand(seen: Seen, newest: int, now: float) -> tuple[Seen, list[str]]:
    if newest > seen.seq:
        return replace(seen, seq=newest, at=now), [MAIL]
    return replace(seen, at=now), []
