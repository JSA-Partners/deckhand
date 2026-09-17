"""Every Claude Code session working on a repository the board holds, read from its transcript.

A transcript is one JSON record per line and runs to tens of megabytes. Everything the captain wants
is within the last few records, so the file is read backwards in chunks and never parsed whole: the
conversation itself is never read at all.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

CHUNK = 262_144
# Enough to reach past a long run of tool output to the records that matter, and no further: a
# transcript whose last deckhand command is older than this is reported without one.
CAP = 4_194_304
FREE = "free"

_COMMAND = re.compile(r"<command-name>/deckhand:([a-z]+)</command-name>")
_ARGS = re.compile(r"<command-args>([^<]*)</command-args>")
_BRANCH_NUMBER = re.compile(r"^[a-z]+-([0-9]+)-")
_NUMBER = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class Pulse:
    """One session as the fleet view sees it; nothing here comes from the conversation."""

    label: str
    session: str
    repo: str
    story: str
    command: str
    idle: float
    cost: float
    waiting: bool
    started: float
    path: Path


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


def _text(record: dict) -> str:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _spoke(record: dict) -> bool:
    """Whether this record is the assistant's own words, which is what waiting on a person looks like."""
    if record.get("type") != "assistant":
        return False
    content = (record.get("message") or {}).get("content")
    parts = content if isinstance(content, list) else []
    return any(isinstance(part, dict) and part.get("type") == "text" for part in parts)


def _answered(record: dict) -> bool:
    """Whether this record is something after the assistant spoke: a person, or a tool coming back."""
    return record.get("type") in {"user", "assistant"} and not _spoke(record)


def _repo_of(cwd: str, repos: dict[str, str]) -> str | None:
    """The repository whose name is a component of `cwd`; a worktree lives under its own clone."""
    parts = Path(cwd).parts
    return next((slug for name, slug in repos.items() if name in parts), None)


def pulse(path: Path, repos: dict[str, str], now: float | None = None) -> Pulse | None:
    """The one line this session is worth, or None when it is not working in a board repository."""
    cwd = branch = ""
    verb = args = ""
    cost = 0.0
    started = 0.0
    waiting: bool | None = None
    for record in records_back(path):
        if not cwd and record.get("cwd"):
            cwd = str(record["cwd"])
            branch = str(record.get("gitBranch") or "")
        if not started and record.get("type") == "cost-state":
            cost = float(record.get("totalCostUSD") or 0.0)
            started = float(record.get("startTime") or 0) / 1000
        if not verb:
            text = _text(record)
            found = _COMMAND.search(text)
            if found is not None:
                verb = found.group(1)
                said = _ARGS.search(text)
                args = said.group(1).strip() if said is not None else ""
        if waiting is None:
            if _spoke(record):
                waiting = True
            elif _answered(record):
                waiting = False
        if cwd and started and verb and waiting is not None:
            break
    repo = _repo_of(cwd, repos) if cwd else None
    if repo is None:
        return None
    if not started:
        started = path.stat().st_mtime
    number = _NUMBER.search(args) if args else None
    from_branch = _BRANCH_NUMBER.match(branch)
    story = number.group(0) if number else (from_branch.group(1) if from_branch else FREE)
    clock = time.time() if now is None else now
    return Pulse(
        label="",
        session=path.stem,
        repo=repo,
        story=story,
        command=f"{verb} {args}".strip() if verb else "-",
        idle=max(clock - path.stat().st_mtime, 0.0),
        cost=cost,
        waiting=bool(waiting),
        started=started,
        path=path,
    )
