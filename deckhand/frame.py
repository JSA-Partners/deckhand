from __future__ import annotations

import textwrap
import unicodedata
from dataclasses import dataclass

from deckhand.board import FLEET_FAILED, START, Board, Row
from deckhand.brief import Glyphs

WIDTH = 110
NARROW = 60
SHORT = 8
AGE = 5
REF = 14
STATE = 28
FIRST = f"no captain has run yet; start one with {START}"
PLAIN = ("queued", "team")
HINT = "type next in the captain to take them in turn"
SGR = {"bold": "1", "dim": "2", "needs": "1;31", "working": "32", "stopped": "1", "done": "2", "stale": "2"}
SGR |= {"warn": "1;33", "command": "36"}

Span = tuple[str, str]


@dataclass(frozen=True)
class Line:
    spans: tuple[Span, ...] = ()
    selected: bool = False
    row: bool = False

    @property
    def text(self) -> str:
        return "".join(text for text, _ in self.spans)


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


def _header(found: Board, g: Glyphs) -> Line:
    if not found.read:
        return Line(((" ", ""), ("Captain", "bold")))
    glyph, words, tone = found.lead
    sep = (g.sep, "dim")
    spans: list[Span] = [(" ", ""), (glyph, tone), (" ", ""), (words, tone)]
    if found.updated:
        spans += [sep, (f"updated {found.updated} ago", "dim")]
    for fact in found.facts:
        spans += [sep, (fact, "")]
    spans += [sep, ("slots ", ""), (found.slots[0], "bold"), (f"/{found.slots[1]}", "dim")]
    return Line(tuple(spans))


def _fleet(found: Board, g: Glyphs) -> list[Line]:
    return [Line(((" ", ""), (g.queued, "dim"), (" ", ""), (FLEET_FAILED, "dim")))] if found.fleet_failed else []


def _banner(found: Board, g: Glyphs, width: int) -> list[Line]:
    if not found.stale:
        return []
    wrapped = textwrap.wrap(found.stale, max(20, width - 3))
    first = Line(((" ", ""), (g.queued, "dim"), (" ", ""), (wrapped[0], "warn")))
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
    paint = "" if tone in PLAIN else tone
    spans: list[Span] = [(" ", ""), (g.queued if tone == "team" else getattr(g, tone), paint), (" ", "")]
    spans.append((_pad(fit(row.ref, ref, g.ellipsis), ref), "bold"))
    if title:
        spans += [(" ", ""), (_pad(fit(row.title, title, g.ellipsis), title), "")]
    spans += [(" ", ""), (_pad(fit(row.state, state, g.ellipsis), state), paint)]
    spans += [(" ", ""), (" " * (AGE - cells(age)) + age, "dim")]
    return Line(tuple(spans), selected, True)


def _details(row: Row, g: Glyphs, columns: tuple[int, int, int], width: int) -> list[Line]:
    indent = " " * (3 + columns[0] + 1)
    lines = []
    for label, value, style in row.detail:
        named = (label + ": ", "dim") if label else ("", "")
        room = max(1, width - cells(indent) - cells(named[0]))
        lines.append(Line(((indent, ""), named, (fit(value, room, g.ellipsis), style))))
    return lines


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
            words = HINT if section.rows else f"{found.quiet}; {found.working} working"
            lines.append(Line(((f"   {words}", "dim"),)))
    return lines, end


def _footer(found: Board, width: int, g: Glyphs) -> list[Line]:
    rows = [row for section in found.sections for row in section.rows]
    keys = [("q", "quit"), ("r", "refresh")]
    keys += [("j/k", "move")] if len(rows) > 1 else []
    keys += [("enter", "details")] if any(row.detail for row in rows) else []
    spans: list[Span] = [(" ", "")]
    for key, action in keys:
        spans += [(key, ""), (f" {action}  ", "dim")]
    return [_rule("", 0, g, width), Line(tuple(spans))]


def _window(body: list[Line], end: int, room: int) -> list[Line]:
    offset = max(0, end - room)
    if any(line.row for line in body[offset + room :]) and end > offset + room - 1:
        offset += 1
    window = body[offset : offset + room]
    if room and any(line.row for line in body[offset + room :]):
        hidden = sum(line.row for line in body[offset + room - 1 :])
        window[-1] = Line(((f"   +{hidden} below", "dim"),))
    return window + [Line()] * (room - len(window))


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
    return Line(tuple(spans), line.selected, line.row)


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
    top = [_header(found, g), *_banner(found, g, width), *_fleet(found, g)]
    if rows is not None and rows < SHORT:
        return [_clip(line, width, g) for line in top[:2][:rows]]
    body, end = _body(found, g, width, selected, expanded)
    footer = _footer(found, width, g) if keys else []
    if rows is not None:
        body = _window(body, end, max(0, rows - len(top) - len(footer)))
    return [_clip(line, width, g) for line in (*top, *body, *footer)]


def text(lines: list[Line], color: bool) -> str:
    if not color:
        return "\n".join(line.text.rstrip() for line in lines)
    painted = (
        "".join(f"\x1b[{SGR[s]}m{t}\x1b[0m" if s in SGR and t.strip() else t for t, s in line.spans) for line in lines
    )
    return "\n".join(line.rstrip() for line in painted)
