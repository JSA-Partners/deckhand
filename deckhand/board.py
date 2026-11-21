from __future__ import annotations

import textwrap
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from deckhand import brief, fleet
from deckhand.brief import Glyphs
from deckhand.order import DONE

WIDTH = 110
NARROW = 60
SHORT = 8
AGE = 5
REF = 14
STATE = 24
START = "/deckhand:captain"
KEYS = (("q", "quit"), ("r", "refresh"), ("j/k", "move"), ("enter", "details"))
COMMANDS = ("next", "hand")
FIRST = f"no captain has run yet; start one with {START}"
SGR = {
    "bold": "1",
    "dim": "2",
    "needs": "1;31",
    "working": "32",
    "queued": "33",
    "done": "2",
    "warn": "1;33",
    "command": "36",
}

Span = tuple[str, str]


@dataclass(frozen=True)
class Line:
    spans: tuple[Span, ...] = ()
    selected: bool = False

    @property
    def text(self) -> str:
        return "".join(text for text, _ in self.spans)


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


def _cell(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def cells(text: str) -> int:
    return sum(_cell(char) for char in text)


def fit(text: str, width: int, ellipsis: str) -> str:
    if cells(text) <= width:
        return text
    room, kept, used = width - cells(ellipsis), "", 0
    if room < 0:
        return ellipsis[:width]
    for char in text:
        if used + _cell(char) > room:
            break
        kept, used = kept + char, used + _cell(char)
    return kept + ellipsis


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - cells(text))


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


def _header(found: Board, g: Glyphs) -> Line:
    spans: list[Span] = [(" ", ""), ("Captain", "bold")]
    if not found.read:
        return Line(tuple(spans))
    spans += [("  ", ""), (found.feature, "") if found.feature else ("no feature open", "dim")]
    sep = (g.sep, "dim")
    if found.day:
        spans += [sep, (found.day, "dim")]
    spans += [sep, ("slots ", ""), (found.slots[0], "bold"), (f"/{found.slots[1]}", "dim")]
    if found.updated:
        spans += [sep, (f"updated {found.updated} ago", "warn" if found.stale else "dim")]
    return Line(tuple(spans))


def _banner(found: Board, g: Glyphs, width: int) -> list[Line]:
    if not found.stale:
        return []
    wrapped = textwrap.wrap(found.stale, max(20, width - 3))
    first = Line(((" ", ""), (g.needs, "warn"), (" ", ""), (wrapped[0], "warn")))
    return [first, *(Line((("   ", ""), (rest, "warn"))) for rest in wrapped[1:])]


def _rule(name: str, count: int, g: Glyphs, width: int) -> Line:
    if not name:
        return Line(((g.rule * width, "dim"),))
    label = f"{name}  {count}"
    fill = g.rule * max(0, width - cells(label) - 4)
    return Line(((g.rule * 2 + " ", "dim"), (name, "bold"), (f"  {count}", ""), (" " + fill, "dim")))


def _columns(found: Board, width: int) -> tuple[int, int, int]:
    rows = [row for section in found.sections for row in section.rows]
    ref = min(REF, max([6, *(cells(row.ref) for row in rows)]))
    fixed = 3 + ref + 1 + 1 + AGE
    if width < NARROW:
        return ref, 0, max(4, width - fixed)
    state = min(STATE, max([8, *(cells(row.state) for row in rows)]))
    return ref, max(8, width - fixed - state - 1), state


def _row(row: Row, g: Glyphs, columns: tuple[int, int, int], selected: bool) -> Line:
    ref, title, state = columns
    tone, age = row.tone, fit(row.age, AGE, "")
    spans: list[Span] = [(" ", ""), (getattr(g, tone), tone), (" ", "")]
    spans.append((_pad(fit(row.ref, ref, g.ellipsis), ref), "bold"))
    if title:
        spans += [(" ", ""), (_pad(fit(row.title, title, g.ellipsis), title), "")]
    spans += [(" ", ""), (_pad(fit(row.state, state, g.ellipsis), state), tone)]
    spans += [(" ", ""), (" " * (AGE - cells(age)) + age, "dim")]
    return Line(tuple(spans), selected)


def _details(row: Row, g: Glyphs, columns: tuple[int, int, int], width: int) -> list[Line]:
    indent = " " * (3 + columns[0] + 1)
    lines = []
    for label, value in row.detail:
        room = max(1, width - cells(indent) - cells(label) - 2)
        style = "command" if label in COMMANDS else ""
        lines.append(Line(((indent, ""), (label + ": ", "dim"), (fit(value, room, g.ellipsis), style))))
    return lines


def _count(found: Board, g: Glyphs) -> Line:
    if not found.read:
        return Line(((f" {FIRST}", "dim"),))
    needs = len(found.sections[0].rows)
    if not needs:
        return Line(((f" nothing needs you; {found.working} working", "dim"),))
    return Line(((" ", ""), (f"{g.needs} {needs} {'needs' if needs == 1 else 'need'} you", "needs")))


def _body(found: Board, g: Glyphs, width: int, selected: str | None, expanded: str | None) -> tuple[list[Line], int]:
    if not found.read:
        return [Line(), Line(((f"   {FIRST}", "dim"),))], 0
    columns, lines, end = _columns(found, width), [], 0
    for section in found.sections:
        lines += [Line(), _rule(section.name, len(section.rows), g, width)]
        for row in section.rows:
            lines.append(_row(row, g, columns, row.key == selected))
            if row.key == expanded:
                lines += _details(row, g, columns, width)
            end = len(lines) if row.key == selected else end
        if section.name == "needs you":
            hint = "type next in the captain to take them in turn"
            words = hint if section.rows else f"nothing needs you; {found.working} working"
            lines.append(Line(((f"   {words}", "dim"),)))
    return lines, end


def _footer(width: int, g: Glyphs) -> list[Line]:
    spans: list[Span] = [(" ", "")]
    for key, action in KEYS:
        spans += [(key, ""), (f" {action}  ", "dim")]
    return [_rule("", 0, g, width), Line(tuple(spans))]


def _clip(line: Line, width: int, g: Glyphs) -> Line:
    if cells(line.text) <= width:
        return line
    spans, room = [], width
    for text, style in line.spans:
        kept = text if cells(text) <= room else fit(text, room, g.ellipsis)
        spans.append((kept, style))
        room -= cells(kept)
        if room <= 0:
            break
    return Line(tuple(spans), line.selected)


def render(
    found: Board,
    g: Glyphs,
    cols: int,
    rows: int | None = None,
    selected: str | None = None,
    expanded: str | None = None,
    keys: bool = False,
) -> list[Line]:
    width = min(cols, WIDTH)
    top = [_header(found, g), *_banner(found, g, width)]
    if rows is not None and rows < SHORT:
        return [_clip(line, width, g) for line in [top[0], _count(found, g)][:rows]]
    body, end = _body(found, g, width, selected, expanded)
    footer = _footer(width, g) if keys else []
    if rows is not None:
        room = max(0, rows - len(top) - len(footer))
        offset = max(0, end - room)
        body = body[offset : offset + room]
        body += [Line()] * (room - len(body))
    return [_clip(line, width, g) for line in (*top, *body, *footer)]


def text(lines: list[Line], color: bool) -> str:
    if not color:
        return "\n".join(line.text.rstrip() for line in lines)
    painted = ("".join(f"\x1b[{SGR[s]}m{t}\x1b[0m" if s and t.strip() else t for t, s in line.spans) for line in lines)
    return "\n".join(line.rstrip() for line in painted)
