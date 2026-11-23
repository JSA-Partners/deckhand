from __future__ import annotations

from deckhand.records import HAND, Record, content_hash

KIND = "permission-gap"
COMMAND_LIMIT = 200


def record(story_id: str, event: str, tool: str, command: str, reason: str) -> Record:
    digest = content_hash("\0".join((event, tool, command)))
    data = {"event": event, "tool": tool, "command": command[:COMMAND_LIMIT], "reason": reason}
    return Record(KIND, f"{story_id}:{digest[:12]}", digest, HAND, data)


def latest(records: list[Record]) -> Record | None:
    return max((r for r in records if r.kind == KIND), key=lambda r: r.at, default=None)
