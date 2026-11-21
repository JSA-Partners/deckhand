from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from deckhand import brief, fleet
from deckhand.order import DONE

START = "/deckhand:captain"


@dataclass(frozen=True)
class Row:
    key: str
    tone: str
    ref: str
    title: str
    state: str
    age: str
    detail: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Section:
    name: str
    rows: tuple[Row, ...]


@dataclass(frozen=True)
class Hand:
    id: str
    status: str
    waiting_for: str
    started_at: str
    spike: bool


@dataclass(frozen=True)
class Board:
    read: bool
    feature: str = ""
    day: str = ""
    slots: tuple[str, str] = ("", "")
    updated: str = ""
    stale: str = ""
    sections: tuple[Section, ...] = ()
    working: int = 0

    def keys(self) -> list[str]:
        return [row.key for section in self.sections for row in section.rows]


def midnight(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _str(value: object) -> str:
    return brief.clean(value) if isinstance(value, str) else ""


def _dicts(found: dict, key: str) -> list[dict]:
    value = found.get(key)
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def _seconds(at: object, now: float) -> float | None:
    try:
        return brief.elapsed(at, now) if isinstance(at, str) else None
    except ValueError:
        return None


def _ago(at: object, now: float) -> str:
    seconds = _seconds(at, now)
    return brief.age(max(0.0, seconds)) if seconds is not None else ""


def _updated(seconds: float) -> str:
    return f"{max(0, int(seconds))}s" if seconds < 60 else brief.age(seconds)


def _stage(found: dict) -> tuple[str, object]:
    stage = found.get("stage")
    if not isinstance(stage, dict) or not isinstance(stage.get("name"), str):
        return "", None
    label = brief.stage_label(stage) if stage.get("cap") else stage["name"]
    return brief.clean(label), stage.get("since")


def _filed(hand: dict) -> Hand:
    spike = _str(hand.get("name")).startswith(fleet.SPIKE)
    return Hand("", "", _str(hand.get("waiting_for")), _str(hand.get("started_at")), spike)


def _held(status: dict, sessions: list[fleet.Session] | None) -> dict[str, Hand]:
    if sessions is None:
        return {_str(h.get("ref")): _filed(h) for h in _dicts(status, "hands") if h.get("state") in fleet.LIVE}
    found = [(ref, s, False) for ref, s in fleet.hands(sessions).items()]
    found += [(ref, s, True) for ref, s in fleet.spikes(sessions).items()]
    return {
        brief.clean(ref): Hand(_str(s.id), _str(s.status), _str(s.waiting_for), s.started_at, spike)
        for ref, s, spike in found
        if fleet.live(s)
    }


def _hand_detail(hand: Hand | None) -> tuple[tuple[str, str], ...]:
    if hand is None:
        return ()
    waiting = (("waiting for", hand.waiting_for),) if hand.waiting_for else ()
    return waiting + ((("hand", f"claude attach {hand.id}"),) if hand.id else ())


def _needs(items: list[dict], by_ref: dict[str, dict], held: dict[str, Hand], now: float) -> list[Row]:
    rows = []
    for item in sorted(items, key=lambda i: (not isinstance(i.get("since"), str), _str(i.get("since")))):
        ref = _str(item.get("ref"))
        title = _str(item.get("title")) or _str(by_ref.get(ref, {}).get("title"))
        nexts = item.get("next") if isinstance(item.get("next"), list) else []
        detail = tuple(("next", _str(command)) for command in nexts if isinstance(command, str))
        key = f"item:{_str(item.get('key')) or ref + _str(item.get('kind'))}"
        age = _ago(item.get("since"), now)
        rows.append(Row(key, "needs", ref, title, _str(item.get("kind")), age, detail + _hand_detail(held.get(ref))))
    return rows


def _working(story: dict, ref: str, hand: Hand, now: float) -> Row:
    label, since = _stage(story)
    state = ", ".join(part for part in (label, "" if hand.status == "busy" else hand.status) if part)
    age = _ago(since, now) or _ago(hand.started_at, now)
    return Row(f"story:{ref}", "working", ref, _str(story.get("title")), state, age, _hand_detail(hand))


def _queued(story: dict, ref: str, full: bool) -> Row:
    blockers = [_str(b) for b in story.get("blocked_by") or [] if isinstance(b, str)]
    state = f"blocked by {', '.join(blockers)}" if blockers else "waits for a slot" if full else "ready"
    label, _ = _stage(story)
    detail = (("owner", _str(story.get("owner")) or "unassigned"),) + ((("stage", label),) if label else ())
    return Row(f"story:{ref}", "queued", ref, _str(story.get("title")), state, "", detail)


def _done(story: dict, ref: str, now: float) -> Row:
    at = story["completed_at"]
    detail = (("merged at", datetime.fromisoformat(at).astimezone().strftime("%H:%M")),)
    return Row(f"story:{ref}", "done", ref, _str(story.get("title")), "merged", _ago(at, now), detail)


def _slots(status: dict, sessions: list[fleet.Session] | None) -> tuple[int, int]:
    slots = status.get("slots") if isinstance(status.get("slots"), dict) else {}
    used, cap = slots.get("used"), slots.get("cap")
    used = fleet.slots_used(sessions) if sessions is not None else used
    return (used if isinstance(used, int) else 0), (cap if isinstance(cap, int) else 0)


def _day(status: dict) -> str:
    budget = status.get("budget")
    if not isinstance(budget, dict):
        return ""
    used, days = budget.get("days_used"), budget.get("budget_days")
    return f"day {used} of {days}" if isinstance(used, int) and isinstance(days, int) else ""


def _stale(status: dict, since: float | None) -> str:
    age = brief.age(since) if since is not None else "an unknown time"
    if status.get("stale_since"):
        return f"stale: Linear was unreachable, this is the state from {age} ago; run {START} to retry"
    if since is None or since > brief.LINE_STALE_SECONDS:
        return f"stale: the captain last read Linear {age} ago; run {START} to refresh"
    return ""


def build(status: dict | None, sessions: list[fleet.Session] | None, now: float, today: float) -> Board:
    if status is None:
        return Board(False)
    items, stories = _dicts(status, "items"), _dicts(status, "stories")
    by_ref = {_str(s.get("ref")): s for s in stories}
    held = _held(status, sessions)
    used, cap = _slots(status, sessions)
    waiting = {_str(i.get("ref")) for i in items}
    working, queued, done = [], [], []
    for found in stories:
        ref = _str(found.get("ref"))
        if found.get("state") == DONE:
            completed = _seconds(found.get("completed_at"), now)
            if completed is not None and now - completed >= today:
                done.append(_done(found, ref, now))
        elif ref in held and ref not in waiting:
            working.append(_working(found, ref, held[ref], now))
        elif ref not in waiting:
            queued.append(_queued(found, ref, used >= cap))
    spikes = [ref for ref, hand in held.items() if hand.spike and ref not in by_ref and ref not in waiting]
    working += [_working({"title": "spike"}, ref, held[ref], now) for ref in spikes]
    since = _seconds(status.get("written_at"), now)
    named = (("needs you", _needs(items, by_ref, held, now)), ("working", working), ("queued", queued))
    sections = tuple(Section(n, tuple(r)) for n, r in (*named, ("done today", done)) if r or n == "needs you")
    updated = _updated(since) if since is not None else ""
    feature, slots = _str(status.get("feature")), (str(used), str(cap))
    return Board(True, feature, _day(status), slots, updated, _stale(status, since), sections, len(working))
