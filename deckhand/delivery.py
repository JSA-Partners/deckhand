from __future__ import annotations

from operator import attrgetter
from typing import TYPE_CHECKING

from deckhand import asks, chores, fleet, queue, stalls, wakes
from deckhand.actions import Action, Resume, Stop
from deckhand.features import Feature
from deckhand.records import epoch
from deckhand.store.state import Message

if TYPE_CHECKING:
    from deckhand.reconcile import _Tick

UNHEARD = 120.0
_started = attrgetter("started_at")


def deliver(tick: _Tick, held: list[Feature]) -> list[Action]:
    waiting: dict[str, list[Message]] = {}
    for message in tick.facts.pending:
        waiting.setdefault(message.hand, []).append(message)
    found = []
    for name, messages in sorted(waiting.items()):
        if name in tick.acted:
            continue
        unheard = [m for m in messages if m.delivered is None and m.seq not in tick.facts.unheard]
        session = tick.alive(name)
        if session is None:
            resume = _resume(tick, held, name, messages)
            if resume is not None:
                found.append(tick.take(resume, slot=True))
        elif unheard and session.state != fleet.WORKING and not session.waiting_for and _deaf(tick, name, unheard):
            if tick.free():
                stop = Stop(name, session.id, "unheard", tuple(m.seq for m in unheard))
                found.append(tick.take(stop, slot=not fleet.holds_slot(session)))
    return found


def _resume(tick: _Tick, held: list[Feature], name: str, messages: list[Message]) -> Resume | None:
    row = tick.facts.hands.get(name)
    last = max((s for s in tick.sessions if s.name == name), key=_started, default=None)
    if row is not None:
        session, cwd = row.session, row.cwd
    else:
        session, cwd = (last.session_id, last.cwd) if last is not None else (None, "")
    if session is None or not tick.listed(session) or not tick.room(name) or tick.backing_off(name):
        return None
    feature, story = story_of(held, name)
    records = feature.records.get(story["id"], []) if feature is not None and story is not None else []
    start = queue.last_start(records)
    if story is not None and start is not None and chores.finished(story, records, start):
        return None
    text, seqs = wakes.prompt(messages), tuple(m.seq for m in messages)
    if any(m.seq in tick.facts.unheard and m.delivered is None for m in messages):
        return Resume(name, session, cwd, text, seqs)
    launch = row.kind if row is not None else str(start.data.get("kind")) if start is not None else queue.STORY_HAND
    restarts = row.restarts if row is not None else None
    if not stalls.auto_restart(records, launch, restarts) and stalls.stall_answer(records, launch) != stalls.RESTART:
        return None
    fresh = asks.hand_start(story, records, launch) if story is not None else None
    return Resume(name, session, cwd, text, seqs, fresh, restart=True)


def story_of(held: list[Feature], name: str) -> tuple[Feature | None, dict | None]:
    if name.startswith(fleet.HAND):
        for feature in held:
            story = next((s for s in feature.live if fleet.hand_name(s["identifier"]) == name), None)
            if story is not None:
                return feature, story
    return None, None


def _deaf(tick: _Tick, name: str, messages: list[Message]) -> bool:
    row = tick.facts.hands.get(name)
    ended = epoch(row.turn_ended) if row is not None and row.turn_ended else 0.0
    oldest = min(epoch(m.written) for m in messages)
    return tick.facts.now - max(oldest, ended, tick.facts.woke) > UNHEARD
