from __future__ import annotations

import textwrap
import unicodedata
from dataclasses import dataclass

from deckhand.board import START, Board, Row
from deckhand.brief import Glyphs

WIDTH = 110
NARROW = 60
SHORT = 8
AGE = 5
REF = 14
STATE = 24
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
