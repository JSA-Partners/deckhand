from __future__ import annotations

import time
from collections.abc import Callable

from deckhand import answers, fleet, queue, status, watch
from deckhand.step import Refusal, own_ref, register
from deckhand.store.local import default_path
from deckhand.store.state import LocalState

USAGE = "watch apply captain|hand blocks and prints one line per event"
POLL = 1.0


Facts = tuple[str | None, bool, float | None, bool, tuple[tuple[str, str], ...]]


# A spike's prompt or stall is queued under the spike's key, which its session's name carries.
def _spikes(read: dict) -> tuple[tuple[str, str], ...]:
    hands = read.get("hands")
    named = [(h.get("name"), h.get("ref")) for h in hands if isinstance(h, dict)] if isinstance(hands, list) else []
    keys = {ref for name, ref in named if isinstance(ref, str) and name == fleet.spike_name(ref)}
    found = []
    for item in read.get("items") or []:
        next = item.get("next")
        attach = [line for line in next if isinstance(line, str)] if isinstance(next, list) else []
        if item["kind"] == queue.SPIKE:
            found.append((item["key"], watch.SPIKE))
        elif item["ref"] in keys:
            found.append((item["key"], " ".join([watch.STUCK + ":", *attach]) if attach else watch.STUCK))
    return tuple(found)


def _captain_facts(rows: LocalState) -> Facts:
    read, top, spikes = status.read(answers.state_dir()), None, ()
    if read is not None:
        items = read.get("items") or []
        top = items[0]["key"] if items else ""
        spikes = _spikes(read)
    beat = rows.heartbeat()
    stale = read is not None and bool(read.get("stale_since"))
    return top, rows.get(watch.HOLD) is not None, beat[1] if beat else None, stale, spikes


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
            top, held, beat, stale, spikes = _captain_facts(rows)
            seen = watch.Seen(top or "", None, 0, self._clock(), stale, spikes=tuple(key for key, _ in spikes))
            beaten = _beat(rows, self._clock())
            while True:
                self._sleep(POLL)
                if self._clock() - beaten >= watch.BEAT_EVERY:
                    beaten = _beat(rows, self._clock())
                if rows.changed():
                    top, held, beat, stale, spikes = _captain_facts(rows)
                seen, lines = watch.captain(seen, top, held, beat, self._clock(), stale, spikes)
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
