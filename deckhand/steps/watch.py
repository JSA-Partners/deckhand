from __future__ import annotations

import time
from collections.abc import Callable

from deckhand import answers, fleet, status, watch
from deckhand.step import Refusal, own_ref, register
from deckhand.store.local import default_path
from deckhand.store.state import LocalState

USAGE = "watch apply captain|hand blocks and prints one line per event"
POLL = 1.0


def _captain_facts(rows: LocalState) -> tuple[str, bool, float | None]:
    items = (status.read(answers.state_dir()) or {}).get("items") or []
    beat = rows.heartbeat()
    return items[0]["key"] if items else "", rows.get(watch.HOLD) is not None, beat[1] if beat else None


def _newest(rows: LocalState, hand: str) -> int:
    return max((m.seq for m in rows.pending(hand) if m.delivered is None), default=0)


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
            top, held, beat = _captain_facts(rows)
            seen = watch.Seen(top, None, 0, self._clock())
            while True:
                self._sleep(POLL)
                if rows.changed():
                    top, held, beat = _captain_facts(rows)
                seen, lines = watch.captain(seen, top, held, beat, self._clock())
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
