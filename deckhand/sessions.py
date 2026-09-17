"""Every Claude Code session working on a repository the board holds, read from its transcript.

A transcript is one JSON record per line and runs to tens of megabytes. Everything the captain wants
is within the last few records, so the file is read backwards in chunks and never parsed whole: the
conversation itself is never read at all.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

CHUNK = 262_144
# Enough to reach past a long run of tool output to the records that matter, and no further: a
# transcript whose last deckhand command is older than this is reported without one.
CAP = 4_194_304


def _record(line: bytes) -> dict | None:
    if not line.strip():
        return None
    try:
        found = json.loads(line)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def records_back(path: Path) -> Iterator[dict]:
    """Every record of the transcript at `path`, newest first, stopping after `CAP` bytes."""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        rest = b""
        read = 0
        while end > 0 and read < CAP:
            size = min(CHUNK, end)
            end -= size
            read += size
            handle.seek(end)
            lines = (handle.read(size) + rest).split(b"\n")
            rest = lines.pop(0)  # the first is a fragment until the chunk before it is read
            for line in reversed(lines):
                record = _record(line)
                if record is not None:
                    yield record
        if end == 0:
            record = _record(rest)
            if record is not None:
                yield record
