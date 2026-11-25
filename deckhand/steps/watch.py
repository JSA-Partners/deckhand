from __future__ import annotations

import time
from collections.abc import Callable

from deckhand import answers, fleet, queue, stage, status, watch
from deckhand.step import Refusal, own_ref, register
from deckhand.store.local import default_path
from deckhand.store.state import LocalState

USAGE = "watch apply captain|hand blocks and prints one line per event"
POLL = 1.0


Rings = tuple[tuple[str, str], ...]
Facts = tuple[str | None, bool, float | None, bool, Rings, Rings]


def _ring(item: dict, stuck: str) -> tuple[str, str]:
    next = item.get("next")
    attach = [line for line in next if isinstance(line, str)] if isinstance(next, list) else []
    return item["key"], " ".join([stuck + ":", *attach]) if attach else stuck


# A spike's prompt or stall is queued under the spike's key, which its session's name carries.
def _spikes(read: dict) -> Rings:
    hands = read.get("hands")
    named = [(h.get("name"), h.get("ref")) for h in hands if isinstance(h, dict)] if isinstance(hands, list) else []
    keys = {ref for name, ref in named if isinstance(ref, str) and name == fleet.spike_name(ref)}
    found = []
    for item in read.get("items") or []:
        if item["kind"] == queue.SPIKE:
            found.append((item["key"], watch.SPIKE))
        elif item["ref"] in keys:
            found.append(_ring(item, watch.STUCK))
    return tuple(found)


# A draft rings under its story and save time, so a redraft rings again; a draft hand's ask rings under its item.
def _drafts(read: dict) -> Rings:
    stories = [s for s in read.get("stories") or [] if isinstance(s, dict)]
    found = [(f"{s['ref']}@{s['draft']}", watch.DRAFT) for s in stories if isinstance(s.get("draft"), str)]
    drafting = {s["ref"] for s in stories if (s.get("stage") or {}).get("name") == stage.DRAFTING}
    found += [_ring(item, watch.DRAFT_STUCK) for item in read.get("items") or [] if item["ref"] in drafting]
    return tuple(found)


def _captain_facts(rows: LocalState) -> Facts:
    read, top, spikes, drafts = status.read(answers.state_dir()), None, (), ()
    if read is not None:
        items = read.get("items") or []
        top = items[0]["key"] if items else ""
        spikes, drafts = _spikes(read), _drafts(read)
    beat = rows.heartbeat()
    stale = read is not None and bool(read.get("stale_since"))
    return top, rows.get(watch.HOLD) is not None, beat[1] if beat else None, stale, spikes, drafts


def _newest(rows: LocalState, hand: str) -> int:
    return max((m.seq for m in rows.pending(hand) if m.delivered is None), default=0)


# A beat that fails is retried on the next poll; the engine shows a watcher that stays silent.
def _beat(rows: LocalState, now: float) -> float:
    try:
        rows.put(watch.WATCHER, {"at": now})
    except Refusal:
        return float("-inf")
    return now


def _say(lines: list[str]) -> None:
    for line in lines:
        print(line, flush=True)


@register("watch")
class Watch:
    def __init__(self, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time) -> None:
        self._sleep = sleep
        self._clock = clock

    def context(self, args: list[str]) -> None:
        print(USAGE)

    def apply(self, args: list[str]) -> None:
        if args == ["captain"]:
            self._captain()
        elif args == ["hand"]:
            self._hand(own_ref())
        else:
            raise Refusal(USAGE)

    def _captain(self) -> None:
        with LocalState(default_path()) as rows:
            rows.changed()
            top, held, beat, stale, spikes, drafts = _captain_facts(rows)
            keys = tuple(key for key, _ in spikes), tuple(key for key, _ in drafts)
            seen = watch.Seen(top or "", None, 0, self._clock(), stale, spikes=keys[0], drafts=keys[1])
            beaten = _beat(rows, self._clock())
            while True:
                self._sleep(POLL)
                if self._clock() - beaten >= watch.BEAT_EVERY:
                    beaten = _beat(rows, self._clock())
                if rows.changed():
                    top, held, beat, stale, spikes, drafts = _captain_facts(rows)
                seen, lines = watch.captain(seen, top, held, beat, self._clock(), stale, spikes, drafts)
                _say(lines)

    def _hand(self, ref: str) -> None:
        if not ref:
            while True:
                self._sleep(POLL)
        name = fleet.session_of(ref)
        with LocalState(default_path()) as rows:
            seen, newest = watch.Seen("", None, 0, self._clock()), 0
            while True:
                self._sleep(POLL)
                if rows.changed():
                    newest = _newest(rows, name)
                seen, lines = watch.hand(seen, newest, self._clock())
                _say(lines)
