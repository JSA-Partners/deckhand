from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from deckhand import fleet

DONE = "Done"
NEEDS, WORKING, STOPPED, QUEUED, MERGED = "needs you", "working", "stopped", "queued", "done today"
TEAM = "team"
ORDER = (NEEDS, WORKING, STOPPED, QUEUED, TEAM, MERGED)
NOT_STARTED = ("drafting", "planning")
FAILED = "failed"


@dataclass(frozen=True)
class Hand:
    ref: str
    state: str
    spike: bool
    id: str = ""
    status: str = ""
    waiting_for: str = ""
    started_at: str = ""
    idle: bool = False
    resuming: bool = False

    @property
    def live(self) -> bool:
        return self.state in fleet.LIVE

    @property
    def holds(self) -> bool:
        return self.resuming or (fleet.holds(self.state, self.waiting_for or None) and not self.idle)


@dataclass(frozen=True)
class Entry:
    kind: str
    ref: str
    story: dict
    item: dict | None = None
    hand: Hand | None = None


def text(found: object, key: str) -> str:
    value = found.get(key) if isinstance(found, dict) else None
    return value if isinstance(value, str) else ""


def dicts(found: object, key: str) -> list[dict]:
    value = found.get(key) if isinstance(found, dict) else None
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def strings(found: object, key: str) -> list[str]:
    value = found.get(key) if isinstance(found, dict) else None
    return [entry for entry in value if isinstance(entry, str)] if isinstance(value, list) else []


def timestamp(at: str) -> float | None:
    try:
        stamp = datetime.fromisoformat(at).timestamp() if at else None
        return stamp if stamp is None or datetime.fromtimestamp(stamp) else None
    except (ValueError, OverflowError, OSError):
        return None


def filed(status: dict) -> dict[str, Hand]:
    return {
        text(h, "ref"): Hand(
            text(h, "ref"),
            text(h, "state"),
            text(h, "name").startswith(fleet.SPIKE),
            waiting_for=text(h, "waiting_for"),
            started_at=text(h, "started_at"),
            idle=h.get("idle") is True,
            resuming=h.get("resuming") is True,
        )
        for h in dicts(status, "hands")
    }


def flagged(status: dict, flag: str) -> frozenset[str]:
    return frozenset(text(h, "ref") for h in dicts(status, "hands") if h.get(flag) is True)


def live(
    sessions: list[fleet.Session], idle: frozenset[str] = frozenset(), resuming: frozenset[str] = frozenset()
) -> dict[str, Hand]:
    held = [(ref, s, False) for ref, s in fleet.hands(sessions).items()]
    held += [(ref, s, True) for ref, s in fleet.spikes(sessions).items()]
    return {
        ref: Hand(
            ref, s.state, spike, s.id, s.status or "", s.waiting_for or "", s.started_at, ref in idle, ref in resuming
        )
        for ref, s, spike in held
    }


def started(story: dict) -> bool:
    name = text(story.get("stage"), "name")
    return bool(name) and name not in NOT_STARTED


def _story(story: dict, hand: Hand | None) -> str:
    if hand is not None:
        return WORKING if hand.holds else STOPPED
    if story.get("mine") is False:
        return TEAM
    return STOPPED if started(story) else QUEUED


def classify(status: dict, hands: dict[str, Hand], today: float | None = None) -> list[Entry]:
    items = sorted(dicts(status, "items"), key=lambda i: (not text(i, "since"), text(i, "since")))
    stories = dicts(status, "stories")
    by_ref = {text(s, "ref"): s for s in stories}
    waiting = {text(i, "ref") for i in items}
    found = {kind: [] for kind in ORDER}
    found[NEEDS] = [
        Entry(NEEDS, text(i, "ref"), by_ref.get(text(i, "ref"), {}), i, hands.get(text(i, "ref"))) for i in items
    ]
    for story in stories:
        ref, hand = text(story, "ref"), hands.get(text(story, "ref"))
        if text(story, "state") == DONE:
            done = timestamp(text(story, "completed_at"))
            if today is not None and done is not None and done >= today:
                found[MERGED].append(Entry(MERGED, ref, story))
        elif ref not in waiting:
            found[_story(story, hand)].append(Entry(_story(story, hand), ref, story, None, hand))
    for ref, hand in hands.items():
        if hand.spike and ref not in waiting and ref not in by_ref and (hand.live or hand.state == FAILED):
            kind = WORKING if hand.holds else STOPPED
            found[kind].append(Entry(kind, ref, {}, None, hand))
    return [entry for kind in ORDER for entry in found[kind]]


def counts(entries: list[Entry]) -> dict[str, int]:
    return {kind: sum(entry.kind == kind for entry in entries) for kind in ORDER}
