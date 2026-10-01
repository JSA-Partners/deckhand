"""What a full captain read leaves behind, and what changed against the one before it.

A person comes back to the fleet after a meeting or a night and asks what moved. The board has no
history of its own, so each full read keeps a snapshot and the next read tells the difference.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from deckhand.config import Settings
from deckhand.sessions import FREE


def path(settings: Settings) -> Path:
    """Where the snapshot for this project lives."""
    return settings.cache / f"captain-{settings.owner}-{settings.project}.json"


def snapshot(stories: dict[str, dict], pulses: dict[str, dict], anomalies: list[str], at: float) -> dict:
    """One read as plain data: stories by `repo#number`, sessions by label, anomalies as lines."""
    return {"at": at, "stories": stories, "sessions": pulses, "anomalies": sorted(anomalies)}


def _rows(value: object, shape: dict[str, type]) -> bool:
    """Whether `value` maps names to dicts that each hold every field of `shape` with its type."""
    return isinstance(value, dict) and all(
        isinstance(row, dict) and all(isinstance(row.get(name), kind) for name, kind in shape.items())
        for row in value.values()
    )


def load(where: Path) -> dict | None:
    """The previous snapshot, or None when there is none or it is not the shape `snapshot` writes."""
    try:
        data = json.loads(where.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or isinstance(data.get("at"), bool) or not isinstance(data.get("at"), int | float):
        return None
    anomalies = data.get("anomalies")
    if not isinstance(anomalies, list) or not all(isinstance(line, str) for line in anomalies):
        return None
    if not _rows(data.get("stories"), {"status": str, "closed": bool}):
        return None
    return data if _rows(data.get("sessions"), {"story": str, "waiting": bool}) else None


def save(where: Path, data: dict) -> None:
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def _label(key: str, repo: str) -> str:
    where, _, number = key.rpartition("#")
    return f"#{number}" if where == repo else key


def _anomaly(line: str, repo: str) -> str:
    key, _, what = line.partition(" ")
    return f"{_label(key, repo)} {what}"


def _order(key: str) -> tuple[str, int]:
    where, _, number = key.rpartition("#")
    return where, int(number or 0)


def _story(story: str) -> str:
    return story if story == FREE else f"#{story}"


def _column(status: str) -> str:
    """The column as a person reads it; an item the board has not given one yet has none."""
    return status or "no column"


def _moved(name: str, was: dict, now: dict) -> str | None:
    flip = "closed" if now["closed"] else "reopened"
    if now["status"] != was["status"]:
        moved = f"{name} {_column(was['status'])} -> {_column(now['status'])}"
        return moved + (f", {flip}" if now["closed"] != was["closed"] else "")
    return f"{name} {flip}" if now["closed"] != was["closed"] else None


def _session(label: str, was: dict | None, now: dict | None) -> str | None:
    if was is None:
        return f"session {label} opened on {_story(now['story'])}"
    if now is None:
        return f"session {label} on {_story(was['story'])} closed"
    if now["waiting"] != was["waiting"]:
        return f"session {label} on {_story(now['story'])} " + ("is waiting on you" if now["waiting"] else "answered")
    return None


def changes(before: dict, after: dict, repo: str) -> list[str]:
    """One line per story, session and anomaly that changed between two snapshots."""
    lines: list[str] = []
    old, new = before["stories"], after["stories"]
    for key in sorted(set(old) | set(new), key=_order):
        now, was = new.get(key), old.get(key)
        name = _label(key, repo)
        if was is None:
            where = f"in {now['status']}" if now["status"] else "without a column"
            lines.append(f"{name} appeared {where}")
        elif now is None:
            lines.append(f"{name} left the board (was {_column(was['status'])})")
        else:
            lines.append(_moved(name, was, now))
    held, holds = before["sessions"], after["sessions"]
    lines += [_session(label, held.get(label), holds.get(label)) for label in sorted(set(held) | set(holds))]
    gone, came = set(before["anomalies"]), set(after["anomalies"])
    lines += [f"new: {_anomaly(line, repo)}" for line in sorted(came - gone)]
    lines += [f"cleared: {_anomaly(line, repo)}" for line in sorted(gone - came)]
    return [line for line in lines if line]


def heading(before: dict, now: float) -> str:
    """`Since 14:02, 3 hours ago` for the time the previous snapshot was taken, with the day past a day."""
    at = float(before["at"])
    clock = time.strftime("%a %H:%M" if now - at >= 86400 else "%H:%M", time.localtime(at))
    hours = int((now - at) // 3600)
    minutes = int((now - at) // 60)
    ago = f"{hours} hours ago" if hours >= 2 else f"{minutes} minutes ago" if minutes >= 2 else "just now"
    return f"Since {clock}, {ago}"
