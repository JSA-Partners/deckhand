from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from deckhand import standing
from deckhand.linear.meter import clock

LINE_LIMIT = 50
LINE_STALE_SECONDS = 30 * 60
SETUP = "run /deckhand:setup"
UNKNOWN = f"cannot tell what needs you: the personal Linear key could not be read; {SETUP}"
UNKNOWN_LINE = "personal key unreadable"
RED, GREEN, DIM, RESET = "\x1b[31m", "\x1b[32m", "\x1b[2m", "\x1b[0m"
PAINT = {"needs": RED, "working": GREEN, "stale": DIM}
MARKERS = "*-+"
ACTIONS = {
    "sign-off": "needs your sign-off",
    "second-sign-off": "needs a second sign-off",
    "finding": "needs your decision",
    "question": "has a question for you",
    "config-edit": "asks to edit its config",
    "prompt": "waits on a permission prompt",
    "stalled": "has a stalled hand",
    "after-merge": "has an after-merge chore for you",
    "planning": "needs planning",
    "spike": "has a spike answer to read",
    "policy": "needs its repository policy fixed",
}


@dataclass(frozen=True)
class Glyphs:
    needs: str
    working: str
    queued: str
    done: str
    stopped: str
    sep: str
    rule: str
    ellipsis: str


UNICODE = Glyphs("●", "◐", "○", "✓", "×", " · ", "─", "…")
ASCII = Glyphs("!", "*", "-", "+", "x", " | ", "-", "...")


def glyphs(encoding: str | None) -> Glyphs:
    return UNICODE if (encoding or "").lower().replace("-", "") == "utf8" else ASCII


def clean(text: str) -> str:
    kept = "".join(" " if unicodedata.category(c)[0] in "CZ" and c != " " else c for c in text)
    return " ".join(kept.split())


def elapsed(at: str, now: float) -> float:
    return now - datetime.fromisoformat(at).timestamp()


def age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{minutes}m"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {rest}m" if rest else f"{hours}h"
    return f"{hours // 24}d"


def stage_label(stage: dict | None) -> str:
    if not stage:
        return ""
    return f"{stage['name']} {stage['round']}" if stage.get("round") else stage["name"]


def unreachable_line(status: dict | None, now: float) -> str:
    if status is None:
        return "Linear unreachable; no earlier state"
    return f"Linear unreachable; showing the state as of {age(elapsed(status['written_at'], now))} ago"


def unknown(status: dict) -> bool:
    return status.get("person_unknown") is True


def nothing(since: float | None, stale: bool) -> str:
    if not stale:
        return "nothing needs you"
    return f"nothing was waiting as of {age(since) if since is not None else 'an unknown time'} ago"


def _waited(item: dict, now: float) -> str:
    return age(elapsed(item["since"], now)) if item.get("since") else ""


def _detail(item: dict, stories: list[dict]) -> str:
    if item.get("head"):
        return clean(item["head"])[:12]
    return next((stage_label(s.get("stage")) for s in stories if s["ref"] == item["ref"]), "")


def _heading(items: list[dict], now: float, g: Glyphs) -> str:
    if len(items) == 1:
        return f"## {g.needs} {clean(items[0]['ref'])} needs you"
    heading = f"## {g.needs} {len(items)} need you"
    timed = [i for i in items if i.get("since")]
    if not timed:
        return heading
    oldest = min(timed, key=lambda i: i["since"])
    return heading + f"{g.sep}oldest {clean(oldest['ref'])} {oldest['kind']} {_waited(oldest, now)}"


def _top(items: list[dict], stories: list[dict], now: float, g: Glyphs) -> str:
    top = items[0]
    line = f"**{top['kind']}**" + (f" {clean(top['ref'])}" if len(items) > 1 else "")
    detail, waited = _detail(top, stories), _waited(top, now)
    line += f" at {detail}" if detail else ""
    line += f"{g.sep}waiting {waited}" if waited else ""
    return line


def _quiet(status: dict, now: float, g: Glyphs) -> str:
    tally = standing.counts(standing.classify(status, standing.filed(status)))
    stopped = tally[standing.STOPPED]
    parts = [f"{g.working} {tally[standing.WORKING]} working"]
    parts += [f"{g.stopped} {stopped} stopped"] if stopped else []
    parts.append(f"{g.queued} {tally[standing.QUEUED]} queued")
    if not status["items"] and not unknown(status):
        parts.append(nothing(elapsed(status["written_at"], now), bool(status.get("stale_since"))))
    parts.append(f"slots {status['slots']['used']}/{status['slots']['cap']}")
    budget = status.get("budget")
    if budget:
        parts.append(f"{clean(budget['feature'])} day {budget['days_used']} of {budget['budget_days']}")
    elif status.get("feature"):
        parts.append(clean(status["feature"]))
    return g.sep.join(parts)


def text(status: dict | None, now: float, g: Glyphs, notes: list[str] = ()) -> str:
    blocks = []
    if status is None or status.get("stale_since"):
        blocks.append(unreachable_line(status, now))
    if status is not None and unknown(status):
        blocks.append(UNKNOWN)
    if status is not None:
        items = status["items"]
        if items:
            blocks += [_heading(items, now, g), _top(items, status["stories"], now, g)]
        blocks.append(_quiet(status, now, g))
    blocks += [clean(note) for note in notes]
    return "\n\n".join("\\" + block if block[:1] in MARKERS and block[1:2] == " " else block for block in blocks)


def colored(env: Mapping[str, str]) -> bool:
    return not env.get("NO_COLOR") and env.get("TERM") != "dumb"


def _line_parts(status: dict, now: float, g: Glyphs, entries: list[standing.Entry] | None) -> tuple[str, str]:
    items = standing.dicts(status, "items")
    if not items:
        entries = standing.classify(status, standing.filed(status)) if entries is None else entries
        working = standing.counts(entries)[standing.WORKING]
        return f"{working} working", "nothing needs you"
    top = items[0]
    waited = _waited(top, now) if standing.timestamp(standing.text(top, "since")) is not None else ""
    head = f"{len(items)} {'needs' if len(items) == 1 else 'need'} you"
    kind, ref = clean(standing.text(top, "kind")), clean(standing.text(top, "ref"))
    fixed = len(g.needs) + 1 + len(head) + len(g.sep) + len(kind) + len(waited) + 2
    ref = ref[: max(1, LINE_LIMIT - fixed)]
    return head, " ".join(part for part in (kind, ref, waited) if part)


def segment(
    status: dict, since: float | None, now: float, g: Glyphs, entries: list[standing.Entry] | None = None
) -> tuple[str, str, str]:
    limited = standing.timestamp(standing.text(status, "limited_until"))
    paused = limited is not None and limited > now
    if since is None or status.get("stale_since") or since > LINE_STALE_SECONDS:
        stale = f"Linear limited until {clock(limited)}" if paused and limited is not None else ""
        return g.queued, stale or f"captain stale {age(since) if since is not None else ''}".rstrip(), "stale"
    if unknown(status):
        return g.needs, f"{UNKNOWN_LINE}{g.sep}{SETUP}", "needs"
    head, tail = _line_parts(status, now, g, entries)
    items = standing.dicts(status, "items")
    glyph, tone = (g.needs, "needs") if items else (g.working, "working")
    if paused and limited is not None:
        limit = f"limited until {clock(limited)}"
        room = LINE_LIMIT - len(glyph) - 1 - len(head) - 2 * len(g.sep) - len(limit)
        ref = clean(standing.text(items[0], "ref"))[: max(1, room)] if items else ""
        return glyph, g.sep.join(part for part in (head, ref, limit) if part), tone
    return glyph, f"{head}{g.sep}{tail}", tone


def line(status: dict | None, now: float, g: Glyphs, color: bool = False) -> str:
    if status is None or not status.get("feature"):
        return ""
    glyph, text, tone = segment(status, elapsed(status["written_at"], now), now, g)
    shown = f"{glyph} {text}"[:LINE_LIMIT]
    return f"{PAINT[tone]}{shown}{RESET}" if color else shown
