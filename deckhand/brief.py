from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime

NOTIFY_LIMIT = 200
DONE = "Done"
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
}


@dataclass(frozen=True)
class Glyphs:
    needs: str
    working: str
    queued: str
    done: str
    sep: str


UNICODE = Glyphs("●", "◐", "○", "✓", " · ")
ASCII = Glyphs("!", "*", "-", "+", " | ")


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
    return f"{stage['name']} {stage['round']}/{stage['cap']}" if stage.get("round") else stage["name"]


def unreachable_line(status: dict | None, now: float) -> str:
    if status is None:
        return "Linear unreachable; no earlier state"
    return f"Linear unreachable; showing the state as of {age(elapsed(status['written_at'], now))} ago"


def _waited(item: dict, now: float) -> str:
    return age(elapsed(item["since"], now)) if item.get("since") else ""


def _detail(item: dict, stories: list[dict]) -> str:
    if item.get("head"):
        return clean(item["head"])[:12]
    return next((stage_label(s["stage"]) for s in stories if s["ref"] == item["ref"]), "")


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
    return line + f"{g.sep}type `next`"


def _quiet(status: dict, working: list[str], g: Glyphs) -> str:
    waiting = {i["ref"] for i in status["items"]}
    open_refs = [s["ref"] for s in status["stories"] if s["state"] != DONE]
    queued = [ref for ref in open_refs if ref not in waiting and ref not in working]
    parts = [f"{g.working} {len(working)} working", f"{g.queued} {len(queued)} queued"]
    parts += [] if status["items"] else ["nothing needs you"]
    parts.append(f"slots {status['slots']['used']}/{status['slots']['cap']}")
    budget = status.get("budget")
    if budget:
        parts.append(f"{clean(budget['feature'])} day {budget['days_used']} of {budget['budget_days']}")
    elif status.get("feature"):
        parts.append(clean(status["feature"]))
    return g.sep.join(parts)


def text(status: dict | None, working: list[str], now: float, g: Glyphs, notes: list[str] = ()) -> str:
    blocks = []
    if status is None or status.get("stale_since"):
        blocks.append(unreachable_line(status, now))
    if status is not None:
        items = status["items"]
        if items:
            blocks += [_heading(items, now, g), _top(items, status["stories"], now, g)]
        blocks.append(_quiet(status, working, g))
    blocks += [clean(note) for note in notes]
    return "\n\n".join(blocks)


def notify(status: dict, now: float) -> tuple[str, list[str]] | None:
    new = [i for i in status["items"] if i["key"] not in status["notified"]]
    if not new:
        return None
    first = new[0]
    waited = _waited(first, now)
    head = f"{clean(first['ref'])} {ACTIONS.get(first['kind'], 'needs you')}" + (
        f" (waiting {waited})" if waited else ""
    )
    more = len(new) - 1
    tail = f"; {more} more {'needs' if more == 1 else 'need'} you" if more else ""
    title = clean(first.get("title") or "")
    room = NOTIFY_LIMIT - 1 - len(head) - len(tail) - 2
    if title and title != first["ref"] and room > 0:
        head += f": {title[:room]}"
    return head + tail, [i["key"] for i in new]
