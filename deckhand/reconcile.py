from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from deckhand import asks, fleet, order, queue, startable, wakes
from deckhand.features import Feature
from deckhand.launch import SPIKE
from deckhand.policy import Policy
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
    clones: Mapping[str, Path] = field(default_factory=dict)
    policies: Mapping[Path, Policy] = field(default_factory=dict)
    unheard: frozenset[int] = frozenset()


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
    record: Record | None = None
    restart: bool = False


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
    seqs: tuple[int, ...] = ()


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
    reserved: set[str] = field(default_factory=set)

    def free(self) -> bool:
        return self.used < self.facts.cap

    def room(self, name: str) -> bool:
        return name in self.reserved or self.free()

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

    def policy_for(self, story: dict) -> Policy:
        return self.facts.policies[startable.clone_of(story, self.facts.clones)]

    def take(self, action: Launch | Resume | Stop, slot: bool) -> Action:
        self.acted.add(action.name)
        if action.name in self.reserved:
            self.reserved.discard(action.name)
        else:
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
        tick = _Tick(facts, sessions, reading.me, _used(facts), set(facts.busy), set(reserved(facts)))
        feature = reading.feature
        if feature is not None:
            found += _finished(tick, feature) + _answers(tick, feature)
            found += _sync(tick, store, feature) + _deliver(tick, feature) + _start(tick, store, feature)
        else:
            found += _deliver(tick, None)
        found += _spikes(tick)
    return [action for action in found if action.name not in facts.busy]


def _holding(facts: Facts) -> set[str]:
    return {s.name for s in facts.read.sessions if fleet.holds_slot(s)}


# A hand stopped as unheard keeps its slot until the Resume that carries its mail.
def reserved(facts: Facts) -> frozenset[str]:
    stopped = {m.hand for m in facts.pending if m.delivered is None and m.seq in facts.unheard}
    return frozenset(name for name in stopped - _holding(facts) - facts.busy if _resumable(facts, name))


def _resumable(facts: Facts, name: str) -> bool:
    sessions = [s for s in facts.read.sessions if s.name == name]
    if any(fleet.alive(s) for s in sessions):
        return False
    row = facts.hands.get(name)
    newest = max(sessions, key=_started, default=None)
    session = row.session if row is not None else newest.session_id if newest is not None else None
    if not session or not any(s.session_id == session for s in facts.read.sessions):
        return False
    feature = facts.reading.feature if facts.reading is not None else None
    story = _story_of(feature, name)
    if feature is None or story is None:
        return True
    records = _records(feature, story)
    start = queue.last_start(records)
    return start is None or not queue.finished(story, records, start)


def _used(facts: Facts) -> int:
    return fleet.slots_used(facts.read.sessions) + len(facts.busy - _holding(facts)) + len(reserved(facts))


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
        if row.short:
            match = [s for s in alive if s.id == row.short and s.session_id != row.session]
        else:
            match = [s for s in alive if s.name == row.name]
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
    for work in startable.startable(store, feature, tick.me, tick.facts.clones):
        if not tick.free():
            break
        if work.clone not in tick.facts.policies:
            continue
        story, records = work.story, _records(feature, work.story)
        name = fleet.hand_name(story["identifier"])
        row = tick.facts.hands.get(name)
        if name in tick.acted or tick.alive(name) is not None or _held_elsewhere(story, records, tick.me):
            continue
        if not startable.due(work, feature, hands, tick.policy_for, row.restarts if row is not None else None):
            continue
        launch = startable.launch_of(work.kind)
        restart = queue.last_start(records, launch) is not None
        if tick.backing_off(name):
            continue
        if row is not None and row.session and row.kind == launch and tick.listed(row.session):
            start = _fresh_start(name, story, records, launch)
            nudge = Resume(name, row.session, row.cwd, wakes.nudge_body(), (), start, restart)
            found.append(tick.take(nudge, slot=True))
        else:
            ref = story["identifier"]
            found.append(tick.take(Launch(name, ref, launch, work.clone, story, tick.me, restart), slot=True))
    return found


def _fresh_start(name: str, story: dict, records: list[Record], launch: str) -> Record:
    newest = max((r.at for r in records), default="")
    return Record(asks.HAND_START, story["id"], newest, HAND, {"kind": launch, "session": name})


def _spikes(tick: _Tick) -> list[Action]:
    found = []
    for row in tick.facts.hands.values():
        parents = Path(row.cwd).parents
        if row.kind != SPIKE or row.short or len(parents) < 2 or tick.alive(row.name) is not None:
            continue
        if tick.free() and row.name not in tick.acted and not tick.backing_off(row.name):
            restart = any(s.name == row.name for s in tick.sessions)
            spike = Launch(row.name, row.ref, SPIKE, parents[1], None, tick.me, restart)
            found.append(tick.take(spike, slot=True))
    return found


def _deliver(tick: _Tick, feature: Feature | None) -> list[Action]:
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
            resume = _resume(tick, feature, name, messages)
            if resume is not None:
                found.append(tick.take(resume, slot=True))
        elif unheard and session.state != WORKING and not session.waiting_for and _deaf(tick, name, unheard):
            if tick.free():
                stop = Stop(name, session.id, "unheard", tuple(m.seq for m in unheard))
                found.append(tick.take(stop, slot=not fleet.holds_slot(session)))
    return found


def _resume(tick: _Tick, feature: Feature | None, name: str, messages: list[Message]) -> Resume | None:
    row = tick.facts.hands.get(name)
    last = max((s for s in tick.sessions if s.name == name), key=_started, default=None)
    if row is not None:
        session, cwd = row.session, row.cwd
    else:
        session, cwd = (last.session_id, last.cwd) if last is not None else (None, "")
    if session is None or not tick.listed(session) or not tick.room(name) or tick.backing_off(name):
        return None
    story = _story_of(feature, name)
    records = _records(feature, story) if feature is not None and story is not None else []
    start = queue.last_start(records)
    if story is not None and start is not None and queue.finished(story, records, start):
        return None
    text, seqs = wakes.prompt(messages), tuple(m.seq for m in messages)
    if any(m.seq in tick.facts.unheard and m.delivered is None for m in messages):
        return Resume(name, session, cwd, text, seqs)
    launch = row.kind if row is not None else str(start.data.get("kind")) if start is not None else queue.STORY_HAND
    restarts = row.restarts if row is not None else None
    if not queue.auto_restart(records, launch, restarts) and queue.stall_answer(records, launch) != queue.RESTART:
        return None
    fresh = _fresh_start(name, story, records, launch) if story is not None else None
    return Resume(name, session, cwd, text, seqs, fresh, restart=True)


def _story_of(feature: Feature | None, name: str) -> dict | None:
    if feature is None or not name.startswith(fleet.HAND):
        return None
    return next((s for s in feature.live if fleet.hand_name(s["identifier"]) == name), None)


def _deaf(tick: _Tick, name: str, messages: list[Message]) -> bool:
    row = tick.facts.hands.get(name)
    ended = epoch(row.turn_ended) if row is not None and row.turn_ended else 0.0
    oldest = min(epoch(m.written) for m in messages)
    return tick.facts.now - max(oldest, ended, tick.facts.woke) > UNHEARD
