from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from deckhand import asks, fleet, order, queue, startable, wakes
from deckhand.features import Feature
from deckhand.launch import SPIKE
from deckhand.reading import Reading
from deckhand.records import HAND, Record, epoch
from deckhand.store import Store
from deckhand.store.state import HandRow, Message

UNHEARD, RETRY_AFTER, RETRY_CAP = 120.0, 60.0, 900.0
IN_PROGRESS, WORKING = "In Progress", "working"


@dataclass(frozen=True)
class Facts:
    now: float
    woke: float
    read: fleet.Fleet
    hands: dict[str, HandRow]
    pending: list[Message]
    reading: Reading | None
    cap: int
    busy: frozenset[str] = frozenset()
    failed: Mapping[str, tuple[int, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class Launch:
    name: str
    ref: str
    kind: str
    clone: Path
    story: dict | None
    me: str
    restart: bool = False


@dataclass(frozen=True)
class Resume:
    name: str
    session: str
    cwd: str
    text: str
    seqs: tuple[int, ...]


@dataclass(frozen=True)
class Post:
    name: str
    key: str
    kind: str
    body: str
    issue: str
    record: Record | None
    restart: bool = False


@dataclass(frozen=True)
class Stop:
    name: str
    short: str
    why: str


@dataclass(frozen=True)
class Remove:
    name: str
    short: str


@dataclass(frozen=True)
class Adopt:
    name: str
    short: str
    session: str


Action = Launch | Resume | Post | Stop | Remove | Adopt


@dataclass
class _Tick:
    facts: Facts
    sessions: list[fleet.Session]
    me: str
    used: int
    acted: set[str] = field(default_factory=set)

    def free(self) -> bool:
        return self.used < self.facts.cap

    def alive(self, name: str) -> fleet.Session | None:
        return max((s for s in self.sessions if s.name == name and fleet.alive(s)), key=_started, default=None)

    def listed(self, session: str | None) -> bool:
        return bool(session) and any(s.session_id == session for s in self.sessions)

    def backing_off(self, name: str) -> bool:
        failed = self.facts.failed.get(name)
        if failed is None:
            return False
        tries, last = failed
        return self.facts.now - last < min(RETRY_AFTER * 2 ** (tries - 1), RETRY_CAP)

    def mail(self, name: str) -> list[Message]:
        return [m for m in self.facts.pending if m.hand == name]

    def take(self, action: Launch | Resume | Stop, slot: bool) -> Action:
        self.acted.add(action.name)
        self.used += slot
        return action


def _started(session: fleet.Session) -> str:
    return session.started_at


def reconcile(facts: Facts, store: Store) -> list[Action]:
    if facts.read.note:
        return []
    removed, sessions = _copies(facts)
    found: list[Action] = [*removed, *_adopt(facts, sessions)]
    reading = facts.reading
    if reading is not None and reading.me is not None:
        tick = _Tick(facts, sessions, reading.me, fleet.slots_used(facts.read.sessions))
        feature = reading.feature
        if feature is not None:
            found += _finished(tick, feature) + _answers(tick, feature)
            found += _sync(tick, store, feature) + _start(tick, store, feature)
        found += _spikes(tick) + _deliver(tick)
    return [action for action in found if action.name not in facts.busy]


def _copies(facts: Facts) -> tuple[list[Remove], list[fleet.Session]]:
    groups: dict[str, list[fleet.Session]] = {}
    for session in facts.read.sessions:
        if fleet.alive(session) and session.name.startswith((fleet.HAND, fleet.SPIKE)):
            groups.setdefault(session.name, []).append(session)
    removed: list[Remove] = []
    for name, group in sorted(groups.items()):
        row = facts.hands.get(name)
        recorded = [s for s in group if row is not None and row.session and s.session_id == row.session]
        kept = recorded[0] if recorded else max(group, key=_started)
        removed += [Remove(name, s.id) for s in group if s is not kept]
    gone = {r.short for r in removed}
    return removed, [s for s in facts.read.sessions if s.id not in gone]


def _adopt(facts: Facts, sessions: list[fleet.Session]) -> list[Action]:
    alive = [s for s in sessions if fleet.alive(s)]
    found: list[Action] = []
    for row in facts.hands.values():
        if row.short and not row.session:
            match = [s for s in alive if s.id == row.short]
        elif not row.short:
            match = [s for s in alive if s.name == row.name]
        else:
            match = []
        if match:
            session = max(match, key=_started)
            found.append(Adopt(row.name, session.id, session.session_id))
    return found


def _records(feature: Feature, story: dict) -> list[Record]:
    return feature.records.get(story["id"], [])


def _finished(tick: _Tick, feature: Feature) -> list[Action]:
    found = []
    for story in feature.live:
        name, records = fleet.hand_name(story["identifier"]), _records(feature, story)
        session, start = tick.alive(name), queue.last_start(records)
        if session is None or session.state == WORKING or start is None:
            continue
        if queue.finished(story, records, start):
            found.append(tick.take(Stop(name, session.id, "finished"), slot=False))
    return found


def _answers(tick: _Tick, feature: Feature) -> list[Action]:
    found: list[Action] = []
    for story in feature.live:
        due = startable.wake_due(feature, story) if story["assignee"] == tick.me else None
        if due is None:
            continue
        version = str(due.data.get("nonce") or due.version)
        wake = Record(asks.WAKE, story["id"], version, HAND, {"decision": due.kind})
        body = wakes.body(story, due, _records(feature, story))
        name = fleet.hand_name(story["identifier"])
        found.append(Post(name, f"wake:{story['id']}:{version}", due.kind, body, story["id"], wake))
    return found


def _sync(tick: _Tick, store: Store, feature: Feature) -> list[Action]:
    found: list[Action] = []
    for done in feature.live:
        if done["state"] != order.DONE or not done.get("completed_at"):
            continue
        labels = set(store.repository_labels(done["team"])) & set(done["labels"])
        for other in feature.live:
            if other["state"] == order.DONE or not labels & set(other["labels"]):
                continue
            name = fleet.hand_name(other["identifier"])
            row = tick.facts.hands.get(name)
            if row is None or row.kind != queue.STORY_HAND or epoch(row.wanted) >= epoch(done["completed_at"]):
                continue
            body = wakes.sync_body(done)
            found.append(Post(name, f"sync:{done['id']}:{name}", wakes.SYNC, body, other["id"], None))
    return found


def _held_elsewhere(story: dict, records: list[Record], me: str) -> bool:
    return story["state"] == IN_PROGRESS and story["assignee"] == me and queue.last_start(records) is None


def _start(tick: _Tick, store: Store, feature: Feature) -> list[Action]:
    hands = fleet.hands(tick.sessions)
    found: list[Action] = []
    for work in startable.startable(store, feature, tick.me):
        if not tick.free():
            break
        story, records = work.story, _records(feature, work.story)
        name = fleet.hand_name(story["identifier"])
        row = tick.facts.hands.get(name)
        if name in tick.acted or tick.alive(name) is not None or _held_elsewhere(story, records, tick.me):
            continue
        if not startable.due(work, feature, hands, restarts=row.restarts if row is not None else None):
            continue
        launch = startable.launch_of(work.kind)
        restart = queue.last_start(records, launch) is not None
        if row is not None and row.kind == launch and tick.listed(row.session):
            if not tick.mail(name):
                found.append(_nudge(name, story, records, launch, restart))
        elif not tick.backing_off(name):
            ref = story["identifier"]
            found.append(tick.take(Launch(name, ref, launch, work.clone, story, tick.me, restart), slot=True))
    return found


def _nudge(name: str, story: dict, records: list[Record], launch: str, restart: bool) -> Post:
    newest = max((r.at for r in records), default="")
    start = Record(asks.HAND_START, story["id"], newest, HAND, {"kind": launch, "session": name})
    key = f"nudge:{story['id']}:{newest}"
    return Post(name, key, wakes.NUDGE, wakes.nudge_body(), story["id"], start, restart)


def _spikes(tick: _Tick) -> list[Action]:
    found = []
    for row in tick.facts.hands.values():
        if row.kind != SPIKE or row.short or any(s.name == row.name for s in tick.sessions):
            continue
        if tick.free() and row.name not in tick.acted and not tick.backing_off(row.name):
            spike = Launch(row.name, row.ref, SPIKE, Path(row.cwd).parents[1], None, tick.me)
            found.append(tick.take(spike, slot=True))
    return found


def _deliver(tick: _Tick) -> list[Action]:
    waiting: dict[str, list[Message]] = {}
    for message in tick.facts.pending:
        if message.delivered is None:
            waiting.setdefault(message.hand, []).append(message)
    found = []
    for name, messages in sorted(waiting.items()):
        if name in tick.acted:
            continue
        session = tick.alive(name)
        if session is None:
            resume = _resume(tick, name, messages)
            if resume is not None:
                found.append(tick.take(resume, slot=True))
        elif session.state != WORKING and not session.waiting_for and _deaf(tick, messages):
            found.append(tick.take(Stop(name, session.id, "unheard"), slot=False))
    return found


def _resume(tick: _Tick, name: str, messages: list[Message]) -> Resume | None:
    row = tick.facts.hands.get(name)
    last = max((s for s in tick.sessions if s.name == name), key=_started, default=None)
    session = row.session if row is not None and row.session else (last.session_id if last else None)
    cwd = row.cwd if row is not None else (last.cwd if last else "")
    if session is None or not tick.listed(session) or not tick.free() or tick.backing_off(name):
        return None
    return Resume(name, session, cwd, wakes.prompt(messages), tuple(m.seq for m in messages))


def _deaf(tick: _Tick, messages: list[Message]) -> bool:
    oldest = min(epoch(m.written) for m in messages)
    return tick.facts.now - max(oldest, tick.facts.woke) > UNHEARD
