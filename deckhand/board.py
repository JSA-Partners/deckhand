from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from deckhand import brief, fleet, standing
from deckhand.brief import Glyphs
from deckhand.standing import Entry, Hand

FLEET_FAILED = "hands from the file, fleet read failed"
NO_CAPTAIN = "no captain running"
CAPTAIN_STATES = {"busy": "working", "idle": "idle", "waiting": "waiting on you"}
TONES = {standing.NEEDS: "needs", standing.WORKING: "working", standing.STOPPED: "stopped"}
TONES |= {standing.QUEUED: "queued", standing.TEAM: "team", standing.MERGED: "done"}

Detail = tuple[str, str, str]


@dataclass(frozen=True)
class Row:
    key: str
    tone: str
    ref: str
    title: str
    state: str
    age: str
    detail: tuple[Detail, ...] = ()


@dataclass(frozen=True)
class Section:
    name: str
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Board:
    read: bool
    lead: tuple[str, str, str] = ("", "", "")
    facts: tuple[str, ...] = ()
    slots: tuple[str, str] = ("", "")
    updated: str = ""
    stale: str = ""
    sections: tuple[Section, ...] = ()
    working: int = 0
    fleet_failed: bool = False
    quiet: str = ""
    captain: str = ""

    def keys(self) -> list[str]:
        return [row.key for section in self.sections for row in section.rows]


def midnight(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _clean(found: object, key: str) -> str:
    return brief.clean(standing.text(found, key))


def _ago(at: str, now: float) -> str:
    stamp = standing.timestamp(at)
    return brief.age(max(0.0, now - stamp)) if stamp is not None else ""


def _number(found: object, key: str) -> int | None:
    value = found.get(key) if isinstance(found, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _stage(story: dict) -> tuple[str, str]:
    stage = story.get("stage")
    name = _clean(stage, "name")
    if not name:
        return "", ""
    round, cap = _number(stage, "round"), _number(stage, "cap")
    return (f"{name} {round}/{cap}" if round and cap else name), standing.text(stage, "since")


def _hand_detail(hand: Hand | None) -> tuple[Detail, ...]:
    if hand is None:
        return ()
    waiting = (("waiting for", brief.clean(hand.waiting_for), ""),) if hand.waiting_for else ()
    return waiting + ((("hand", f"claude attach {brief.clean(hand.id)}", "command"),) if hand.id else ())


def _needs(entry: Entry, now: float) -> Row:
    item, ref = entry.item or {}, brief.clean(entry.ref)
    kind = _clean(item, "kind")
    action = f"{brief.ACTIONS.get(kind, 'needs you')}; the captain will ask"
    key = f"item:{_clean(item, 'key') or ref + kind}"
    title = _clean(item, "title") or _clean(entry.story, "title")
    detail = (("", action, ""), *_hand_detail(entry.hand))
    return Row(key, "needs", ref, title, kind, _ago(standing.text(item, "since"), now), detail)


def _hand_row(entry: Entry, now: float) -> Row:
    hand, ref = entry.hand, brief.clean(entry.ref)
    label, since = _stage(entry.story)
    title = _clean(entry.story, "title") or ("spike" if hand and hand.spike else "")
    if entry.kind == standing.WORKING and hand is not None:
        own = brief.clean(hand.status)
        state = ", ".join(part for part in (label, "" if own == "busy" else own) if part)
    else:
        gone = "idle" if hand and hand.idle else (brief.clean(hand.state) if hand else "") or "stopped"
        state = f"{gone} at {label}" if label else gone
    age = _ago(since, now) or (_ago(hand.started_at, now) if hand else "")
    return Row(f"story:{ref}", TONES[entry.kind], ref, title, state, age, _hand_detail(hand))


def _queued(entry: Entry, full: bool) -> Row:
    story, ref = entry.story, brief.clean(entry.ref)
    blockers = [brief.clean(b) for b in standing.strings(story, "blocked_by")]
    state = f"blocked by {', '.join(blockers)}" if blockers else "waits for a slot" if full else "ready"
    label, _ = _stage(story)
    detail = (("owner", _clean(story, "owner") or "unassigned", ""),) + ((("stage", label, ""),) if label else ())
    return Row(f"story:{ref}", "queued", ref, _clean(story, "title"), state, "", detail)


def _team(entry: Entry, now: float, g: Glyphs) -> Row:
    story, ref = entry.story, brief.clean(entry.ref)
    label, since = _stage(story)
    state = g.sep.join(part for part in (_clean(story, "owner") or "a teammate", label) if part)
    return Row(f"story:{ref}", "team", ref, _clean(story, "title"), state, _ago(since, now))


def _done(entry: Entry, now: float) -> Row:
    at, ref = standing.text(entry.story, "completed_at"), brief.clean(entry.ref)
    stamp = standing.timestamp(at) or 0.0
    detail = (("merged at", datetime.fromtimestamp(stamp).strftime("%H:%M"), ""),)
    return Row(f"story:{ref}", "done", ref, _clean(entry.story, "title"), "merged", _ago(at, now), detail)


def _row(entry: Entry, now: float, full: bool, g: Glyphs) -> Row:
    if entry.kind == standing.NEEDS:
        return _needs(entry, now)
    if entry.kind == standing.QUEUED:
        return _queued(entry, full)
    if entry.kind == standing.TEAM:
        return _team(entry, now, g)
    return _done(entry, now) if entry.kind == standing.MERGED else _hand_row(entry, now)


def _slots(status: dict, read: fleet.Fleet | None) -> tuple[int, int]:
    slots = status.get("slots")
    used = fleet.slots_used(read.sessions) if read is not None and not read.note else _number(slots, "used")
    return used or 0, _number(slots, "cap") or 0


def _facts(status: dict) -> tuple[str, ...]:
    budget = status.get("budget")
    used, days = _number(budget, "days_used"), _number(budget, "budget_days")
    day = f"day {used} of {days}" if used is not None and days is not None else ""
    return tuple(part for part in (_clean(status, "feature"), day) if part)


def _stale(status: dict, since: float | None) -> str:
    if not status.get("stale_since"):
        return ""
    age = brief.age(since) if since is not None else "an unknown time"
    return f"stale: Linear is unreachable, this is the state from {age} ago"


def _captain(read: fleet.Fleet | None) -> str:
    if read is None or read.note:
        return ""
    found = fleet.captain(read.sessions)
    if found is None:
        return NO_CAPTAIN
    return f"captain {CAPTAIN_STATES.get(found.status or '', found.status)}"


def _updated(seconds: float | None) -> str:
    if seconds is None:
        return ""
    return f"{max(0, int(seconds))}s" if seconds < 60 else brief.age(seconds)


def build(status: dict | None, read: fleet.Fleet | None, now: float, today: float, g: Glyphs) -> Board:
    if not isinstance(status, dict):
        return Board(False)
    live = read is not None and not read.note
    hands = standing.live(read.sessions, standing.idle(status)) if live else standing.filed(status)
    entries = standing.classify(status, hands, today)
    used, cap = _slots(status, read)
    rows: dict[str, list[Row]] = {kind: [] for kind in standing.ORDER}
    for entry in entries:
        rows[entry.kind].append(_row(entry, now, used >= cap, g))
    sections = tuple(Section(k, tuple(r)) for k, r in rows.items() if r or k == standing.NEEDS)
    stamp = standing.timestamp(standing.text(status, "written_at"))
    since = now - stamp if stamp is not None else None
    stale = _stale(status, since)
    old = bool(stale) or since is None or since > brief.LINE_STALE_SECONDS
    lead = brief.segment(status, since, now, g, entries)
    working = len(rows[standing.WORKING])
    updated = "" if old else _updated(since)
    failed = read is not None and bool(read.note)
    quiet = brief.UNKNOWN if brief.unknown(status) else brief.nothing(since, old)
    slots = (str(used), str(cap))
    return Board(True, lead, _facts(status), slots, updated, stale, sections, working, failed, quiet, _captain(read))
