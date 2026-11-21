from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from deckhand import fleet
from deckhand.queue import Item
from deckhand.records import content_hash

FILE = "captain.json"
SHAPE = 1


def item_key(item: Item) -> str:
    return content_hash(json.dumps([item.kind, item.ref, item.at, item.next]))[:12]


def _item(item: Item) -> dict:
    keys = {"key": item_key(item), "kind": item.kind, "ref": item.ref, "title": item.title}
    return keys | {"since": item.at or None, "next": item.next}


def _hands(sessions: list[fleet.Session]) -> list[dict]:
    held = [*sorted(fleet.hands(sessions).items()), *sorted(fleet.spikes(sessions).items())]
    return [
        {"name": s.name, "ref": ref, "state": s.state, "waiting_for": s.waiting_for, "started_at": s.started_at}
        for ref, s in held
    ]


def snapshot(
    written_at: str, team: str, facts: dict, waiting: list[Item], sessions: list[fleet.Session], previous: dict | None
) -> dict:
    items = [_item(item) for item in waiting]
    keys = {item["key"] for item in items}
    notified = [key for key in (previous or {}).get("notified", []) if key in keys]
    return {
        "shape": SHAPE,
        "written_at": written_at,
        "team": team,
        "feature": facts["feature"],
        "budget": facts["budget"],
        "slots": facts["slots"],
        "items": items,
        "stories": facts["order"],
        "hands": _hands(sessions),
        "release": facts["release"],
        "stale_since": None,
        "notified": notified,
    }


def write(state: Path, status: dict) -> None:
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    fd, temp = tempfile.mkstemp(dir=state, prefix=f".{FILE}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(status, handle, indent=2)
        os.replace(temp, state / FILE)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def read(state: Path) -> dict | None:
    try:
        found = json.loads((state / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict) or found.get("shape") != SHAPE:
        return None
    notified = found.get("notified")
    return found if isinstance(notified, list) and all(isinstance(key, str) for key in notified) else None
