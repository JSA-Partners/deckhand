from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date

from deckhand import fleet, idle, queue, reading, status
from deckhand.linear.client import LinearError
from deckhand.lines import Lines
from deckhand.linker import Linked
from deckhand.reading import Reading
from deckhand.reconcile import Facts, reserved, used
from deckhand.records import iso
from deckhand.step import Refusal
from deckhand.store.state import HandRow, LocalState
from deckhand.worker import problem


# Only a launch, resume or stop in flight holds a story; a posted message or an adoption does not.
def held(facts: Facts, holding: frozenset[str]) -> tuple[frozenset[str], int, frozenset[str]]:
    resuming = reserved(replace(facts, busy=frozenset()))
    nudged = idle.nudged(facts.pending, facts.now, facts.woke)
    quiet = frozenset(fleet.ref_of(name) for name in resuming | nudged | holding)
    return quiet, used(replace(facts, busy=holding, holds=holding)), resuming


class Shown:
    def __init__(self, team: str, state: LocalState, lines: Lines, today: Callable[[], date]) -> None:
        self._team, self._state, self._lines, self._today = team, state, lines, today
        self._fleet: fleet.Fleet | None = None
        self._quiet: frozenset[str] = frozenset()
        self._idle: frozenset[str] = frozenset()
        self._used: int | None = None
        self._limit: str | None = None

    def moved(self, read: fleet.Fleet, quiet: frozenset[str], idle: frozenset[str], slots: int) -> bool:
        return read != self._fleet or quiet != self._quiet or idle != self._idle or slots != self._used

    def stale(self, at: str) -> None:
        previous = status.get(self._state)
        if previous is not None:
            self._write(status.stale(previous, at), previous)

    def limit(self, limit: str | None) -> None:
        previous = status.get(self._state) if limit != self._limit else None
        if previous is not None:
            self._write(previous | {"limited_until": limit}, previous)
            self._limit = limit

    def read(
        self,
        good: Linked,
        read: fleet.Fleet,
        hands: dict[str, HandRow],
        idle: frozenset[str],
        quiet: frozenset[str],
        slots: tuple[int, frozenset[str]],
        limit: str | None,
        stale_at: str | None,
    ) -> Reading | None:
        stores = good.stores
        assert stores is not None
        restarts = {row.ref: row.restarts for row in hands.values()}
        try:
            found = reading.read(
                stores("app"),
                lambda: stores("person"),
                self._team,
                read,
                good.local,
                self._today(),
                quiet=quiet,
                restarts=restarts,
                idle=frozenset(fleet.ref_of(name) for name in idle),
            )
        except (LinearError, Refusal) as error:
            self._lines.say("read", f"cannot read: {problem(error)}")
            return None
        self._fleet, self._quiet, self._idle, self._used = read, quiet, idle, slots[0]
        self._lines.clear("read")
        previous = status.get(self._state)
        waiting = queue.ranked([*queue.policy_items(found.feature, good.local.clones, good.broken), *found.waiting])
        at = iso(int(good.at * 1000))
        shown = status.snapshot(at, self._team, found.facts, waiting, read.sessions, found.me is None, idle, *slots)
        shown["limited_until"] = self._limit = limit
        if stale_at is not None:
            shown["stale_since"] = (previous or {}).get("stale_since") or stale_at
        self._write(shown, previous)
        return found

    def _write(self, shown: dict, previous: dict | None) -> None:
        if shown == previous:
            return
        try:
            status.put(self._state, shown)
        except Refusal as error:
            self._lines.say("snapshot", f"cannot write the snapshot: {error.rule}")
        else:
            self._lines.clear("snapshot")
