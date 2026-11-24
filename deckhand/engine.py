from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import IO, Protocol

from deckhand import acts, answers, config, fleet, git, reading, status
from deckhand.linear.client import LinearError
from deckhand.policy import Policy
from deckhand.reading import Reading
from deckhand.reconcile import RETRY_AFTER, RETRY_CAP, Action, Facts, Launch, Remove, Resume, reconcile, reserved
from deckhand.records import Record, epoch, iso
from deckhand.step import Refusal
from deckhand.store.open import Stores
from deckhand.store.state import HEARTBEAT, SLEEP_GAP, HandRow, LocalState
from deckhand.worker import problem

FLEET_EVERY, LINEAR_EVERY, RECONCILE_EVERY, HEARTBEAT_EVERY = 5.0, 60.0, 1.0, 5.0
LOCK = "engine.lock"
READING = "reading Linear"
ENGINE = "engine"


@dataclass(frozen=True)
class Linked:
    stores: Stores | None
    reading: Reading | None
    at: float
    error: str
    local: config.LocalConfig = field(default_factory=config.LocalConfig)
    policies: Mapping[Path, Policy] = field(default_factory=dict)
    problems: tuple[str, ...] = ()


def lock(state_dir: Path) -> IO[str] | None:
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = (state_dir / LOCK).open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.truncate(0)
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def _configured(problems: list[str]) -> tuple[config.LocalConfig, dict[Path, Policy]]:
    try:
        local = config.local()
    except Refusal as error:
        problems.append(error.rule)
        local = config.LocalConfig()
    policies: dict[Path, Policy] = {}
    for clone in local.clones.values():
        try:
            policies[clone] = Policy.load(clone)
        except Refusal as error:
            problems.append(f"{error.rule}; its stories do not start")
    return local, policies


def link(
    team: str,
    fleet_now: Callable[[], fleet.Fleet],
    clock: Callable[[], float],
    stores: Callable[[], Stores] = Stores,
    today: Callable[[], date] = date.today,
) -> Linked:
    at, problems = clock(), []
    local, policies = _configured(problems)
    held = stores()
    try:
        found = reading.read(held("app"), lambda: held("person"), team, fleet_now(), local, today())
    except (LinearError, Refusal) as error:
        return Linked(None, None, at, problem(error), local, policies, tuple(problems))
    return Linked(held, found, at, "", local, policies, tuple(problems))


class Polled[T](Protocol):
    def latest(self) -> T | None: ...

    def began(self) -> float | None: ...

    def poke(self) -> None: ...

    def stop(self) -> None: ...


class Works(Protocol):
    def submit(self, action: Action) -> None: ...

    def busy(self) -> frozenset[str]: ...

    def results(self) -> list[tuple[Action, str | None]]: ...

    def stop(self) -> None: ...


def _backing_off(tries: int, last: float, now: float) -> bool:
    return now - last < min(RETRY_AFTER * 2 ** (tries - 1), RETRY_CAP)


class Engine:
    def __init__(
        self,
        team: str,
        state: LocalState,
        fleets: Polled[fleet.Fleet],
        linear: Polled[Linked],
        worker: Works,
        clock: Callable[[], float] = time.time,
        today: Callable[[], date] = date.today,
        pid: int | None = None,
    ) -> None:
        self._team, self._state, self._fleets, self._linear, self._worker = team, state, fleets, linear, worker
        self._clock, self._today, self._pid = clock, today, pid if pid is not None else os.getpid()
        self._folder = answers.state_dir()
        self._woke = clock()
        self._ticked: float | None = None
        self._beat = self._reconciled = float("-inf")
        self._newest: Linked | None = None
        self._good: Linked | None = None
        self._reading: Reading | None = None
        self._fleet: fleet.Fleet | None = None
        self._quiet: frozenset[str] = frozenset()
        self._settling: dict[str, float] = {}
        self._failed: dict[str, tuple[int, float]] = {}
        self._unremoved: dict[str, tuple[Remove, int, float]] = {}
        self._lines: dict[str, str] = {}
        self._starting: set[str] = set()
        self._poked_for = 0.0
        self._poked_at: float | None = None

    def tick(self) -> None:
        now = self._clock()
        woke, self._ticked = self._ticked is not None and now - self._ticked > SLEEP_GAP, now
        if woke:
            self._woke = now
            self.refresh()
            self._heartbeat(now)
            return
        if now - self._beat >= HEARTBEAT_EVERY:
            self._heartbeat(now)
        try:
            self._step(now)
        except Refusal as error:
            self._say(ENGINE, f"cannot read the state: {error.rule}")

    def _step(self, now: float) -> None:
        changed = self._state.changed()
        hands = self._state.hands()
        linked = self._take_link(now)
        self._poke_on_turn_end(hands, now)
        busy = self._collect(now)
        read, good = self._fleets.latest(), self._good
        if read is None or good is None or good.stores is None:
            return
        due = now - self._reconciled >= RECONCILE_EVERY
        held = reserved(self._facts(now, read, hands, busy)) | self._starting | set(self._settling)
        quiet = frozenset(fleet.ref_of(name) for name in held)
        fresh = self._fresh_records(good) if due else None
        moved = changed or read != self._fleet or quiet != self._quiet or fresh is not None
        if (moved or linked) and not self._read(good, read, hands, quiet, now) and fresh is not None:
            self._keep_records(fresh)
        began = self._fleets.began()
        if due and began is not None and began > self._woke and self._reading is not None:
            self._reconciled = now
            self._reconcile(good, self._facts(now, read, hands, busy), read, busy)

    def _heartbeat(self, now: float) -> None:
        try:
            self._state.beat(self._pid, now)
        except Refusal as error:
            self._say(ENGINE, f"cannot write the heartbeat: {error.rule}")
        else:
            self._beat = now

    def _take_link(self, now: float) -> bool:
        newest = self._linear.latest()
        if newest is None or newest is self._newest:
            return False
        self._newest = newest
        if not newest.error:
            self._good, self._reading = newest, newest.reading
            return True
        previous = status.read(self._folder)
        if previous is not None:
            self._write(status.stale(previous, self._iso(now)), previous)
        return False

    def _poke_on_turn_end(self, hands: dict[str, HandRow], now: float) -> None:
        ended = max((epoch(row.turn_ended) for row in hands.values() if row.turn_ended), default=0.0)
        last = self._newest.at if self._newest is not None else float("-inf")
        if ended > last and ended > self._poked_for:
            self._poked_for, self._poked_at = ended, now
            self._linear.poke()

    def _collect(self, now: float) -> frozenset[str]:
        busy = self._worker.busy()
        for action, line in self._worker.results():
            if isinstance(action, (Launch, Resume)):
                self._started(action.name, line, now)
            elif isinstance(action, Remove):
                self._removed(action, line, now)
            key = f"{type(action).__name__}:{action.name}"
            if line is None:
                self._lines.pop(key, None)
            else:
                self._say(key, line)
        began = self._fleets.began()
        self._settling = {n: at for n, at in self._settling.items() if began is None or began <= at}
        return busy | frozenset(self._settling)

    def _started(self, name: str, line: str | None, now: float) -> None:
        self._starting.discard(name)
        self._settling[name] = now
        self._fleets.poke()
        if line is None:
            self._failed.pop(name, None)
        else:
            self._failed[name] = (self._failed.get(name, (0, now))[0] + 1, now)

    def _removed(self, action: Remove, line: str | None, now: float) -> None:
        if line is None:
            self._unremoved.pop(action.short, None)
            return
        tries = self._unremoved[action.short][1] + 1 if action.short in self._unremoved else 1
        self._unremoved[action.short] = (action, tries, now)

    def _say(self, key: str, line: str) -> None:
        self._lines.pop(key, None)
        self._lines[key] = line

    def _facts(self, now: float, read: fleet.Fleet, hands: dict[str, HandRow], busy: frozenset[str]) -> Facts:
        good = self._good
        local = good.local if good is not None else config.LocalConfig()
        return Facts(
            now=now,
            woke=self._woke,
            read=read,
            hands=hands,
            pending=self._state.pending(),
            reading=self._reading,
            cap=local.slot_cap,
            busy=busy,
            failed=dict(self._failed),
            clones=local.clones,
            policies=good.policies if good is not None else {},
            unheard=acts.unheard(self._state),
        )

    def _fresh_records(self, good: Linked) -> dict[str, list[Record]] | None:
        feature = self._reading.feature if self._reading is not None else None
        if feature is None or good.stores is None:
            return None
        try:
            fresh = {s["id"]: good.stores("app").records(s["id"]) for s in feature.stories}
        except Refusal:
            return None
        return None if fresh == {s["id"]: feature.records.get(s["id"], []) for s in feature.stories} else fresh

    # A read that misses the memo while Linear is down still hands reconcile the records written since.
    def _keep_records(self, fresh: dict[str, list[Record]]) -> None:
        held = self._reading
        if held is not None and held.feature is not None:
            self._reading = replace(held, feature=replace(held.feature, records=fresh))

    def _read(
        self, good: Linked, read: fleet.Fleet, hands: dict[str, HandRow], quiet: frozenset[str], now: float
    ) -> bool:
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
            )
        except (LinearError, Refusal) as error:
            self._say(ENGINE, f"cannot read: {problem(error)}")
            return False
        self._reading, self._fleet, self._quiet = found, read, quiet
        self._lines.pop(ENGINE, None)
        previous = status.read(self._folder)
        shown = status.snapshot(
            self._iso(good.at), self._team, found.facts, found.waiting, read.sessions, previous, found.me is None
        )
        if self._newest is not None and self._newest.error:
            shown["stale_since"] = (previous or {}).get("stale_since") or self._iso(now)
        self._write(shown, previous)
        return True

    def _write(self, shown: dict, previous: dict | None) -> None:
        if shown == previous:
            return
        try:
            status.write(self._folder, shown)
        except Refusal as error:
            self._say(ENGINE, f"cannot write the snapshot: {error.rule}")

    def _reconcile(self, good: Linked, facts: Facts, read: fleet.Fleet, busy: frozenset[str]) -> None:
        assert good.stores is not None
        try:
            found = reconcile(facts, good.stores("app"))
        except (LinearError, Refusal) as error:
            self._say(ENGINE, f"cannot reconcile: {problem(error)}")
            found = []
        for action in [*found, *self._retries(read, busy, facts.now)]:
            if isinstance(action, (Launch, Resume)):
                self._starting.add(action.name)
            self._worker.submit(action)

    # A copy stopped but not removed is no longer alive, so reconcile never names it again.
    def _retries(self, read: fleet.Fleet, busy: frozenset[str], now: float) -> list[Remove]:
        if read.note:
            return []
        listed = {s.id: s for s in read.sessions}
        self._unremoved = {short: held for short, held in self._unremoved.items() if short in listed}
        return [
            action
            for short, (action, tries, last) in self._unremoved.items()
            if not fleet.alive(listed[short]) and action.name not in busy and not _backing_off(tries, last, now)
        ]

    def refresh(self) -> None:
        self._poked_at = self._clock()
        self._fleets.poke()
        self._linear.poke()

    def note(self) -> str:
        newest = self._newest
        if self._poked_at is not None and (newest is None or newest.at < self._poked_at):
            return READING
        if self._lines:
            return list(self._lines.values())[-1]
        if newest is not None and newest.error:
            return f"cannot read Linear: {newest.error}"
        good = self._good
        return "; ".join(good.problems) if good is not None else ""

    def stop_all(self, runner: git.Runner = git.run) -> None:
        for session in fleet.sessions(runner).sessions:
            if fleet.alive(session) and session.name.startswith((fleet.HAND, fleet.SPIKE)):
                try:
                    git.call(["claude", "stop", session.id], None, runner)
                except git.GitError as error:
                    self._say(f"Stop:{session.name}", f"{fleet.ref_of(session.name)}: {error.stderr_tail}")

    def close(self) -> None:
        try:
            self._state.drop(HEARTBEAT)
        finally:
            for running in (self._fleets, self._linear, self._worker):
                running.stop()
            self._state.close()

    def _iso(self, at: float) -> str:
        return iso(int(at * 1000))
