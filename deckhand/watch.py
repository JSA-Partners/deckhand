from __future__ import annotations

from dataclasses import dataclass, replace

from deckhand import fleet
from deckhand.records import epoch
from deckhand.step import Refusal
from deckhand.store import state

QUEUE = "deckhand: something needs the person"
STOPPED = "deckhand: the engine stopped"
RUNNING = "deckhand: the engine is running"
MAIL = "deckhand: a message waits"
SPIKE = "deckhand: a spike was answered"
STUCK = "deckhand: a spike needs the person"
HOLD = "hold"
WATCHER = "watcher"
BEAT_EVERY = 10.0
DEAF = "the captain cannot hear the engine; close the captain and open it again"


@dataclass(frozen=True)
class Seen:
    top: str
    engine: bool | None
    seq: int
    at: float
    stale: bool = False
    recovered: str = ""
    spikes: tuple[str, ...] = ()


# The captain withholds every item while the engine is down, so an item still on top rings when it returns.
# A top of None is a snapshot that could not be read: it neither rings nor clears what was shown.
# spikes pairs each spike item's key with its ring line; while held, only a spike item not seen before rings.
def captain(
    seen: Seen,
    top: str | None,
    held: bool,
    beat: float | None,
    now: float,
    stale: bool = False,
    spikes: tuple[tuple[str, str], ...] = (),
) -> tuple[Seen, list[str]]:
    if now - seen.at > state.SLEEP_GAP:
        return replace(seen, at=now), []
    lines = []
    engine = state.alive(beat, now)
    shown = "" if seen.engine is False and engine else seen.top
    # A Linear error ends the captain's turn unasked, so its top rings once more when Linear reads clean.
    recovered = seen.recovered
    if top is not None and seen.stale and not stale and top != recovered:
        shown, recovered = "", top
    if top == "":
        shown = ""
    elif top is not None and top != shown and not held:
        lines.append(QUEUE)
        shown = top
    heard = seen.spikes
    if top is not None:
        if held:
            lines += [line for key, line in spikes if key not in heard]
        heard = tuple(key for key, _ in spikes)
    if seen.engine is not None and engine != seen.engine:
        lines.append(RUNNING if engine else STOPPED)
    return Seen(shown, engine, seen.seq, now, seen.stale if top is None else stale, recovered, heard), lines


def hand(seen: Seen, newest: int, now: float) -> tuple[Seen, list[str]]:
    if newest > seen.seq:
        return replace(seen, seq=newest, at=now), [MAIL]
    return replace(seen, at=now), []


def heard(rows: state.LocalState) -> float | None:
    try:
        body = rows.get(WATCHER)
    except Refusal:
        return None
    at = body.get("at") if isinstance(body, dict) else None
    return float(at) if isinstance(at, int | float) and not isinstance(at, bool) else None


# A captain or an engine just started has had no time to hear its first beat.
def deaf(read: fleet.Fleet | None, beat: float | None, now: float, since: float) -> bool:
    captain = fleet.captain(read.sessions) if read is not None and not read.note else None
    if captain is None or state.alive(beat, now):
        return False
    started = epoch(captain.started_at) if captain.started_at else now
    return now - max(started, since) > state.STALE_AFTER
