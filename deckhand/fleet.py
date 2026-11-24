from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from deckhand import git
from deckhand.records import iso

COMMAND = ["claude", "agents", "--json", "--all"]
HAND, SPIKE, CAPTAIN = "hand-", "spike-", "captain"
LIVE = ("working", "blocked")
FAILED = ("failed", "stopped")
PERMISSION = "permission prompt"


@dataclass(frozen=True)
class Session:
    id: str
    name: str
    session_id: str
    cwd: str
    started_at: str
    state: str
    status: str | None
    waiting_for: str | None


@dataclass(frozen=True)
class Fleet:
    sessions: list[Session] = field(default_factory=list)
    note: str = ""


def _text(row: dict, name: str) -> str:
    value = row.get(name)
    return value if isinstance(value, str) else ""


def _optional(row: dict, name: str) -> str | None:
    value = row.get(name)
    return value if isinstance(value, str) else None


def _session(row: dict) -> Session:
    started = row.get("startedAt")
    return Session(
        id=_text(row, "id"),
        name=_text(row, "name"),
        session_id=_text(row, "sessionId"),
        cwd=_text(row, "cwd"),
        started_at=iso(started) if isinstance(started, int) and not isinstance(started, bool) else "",
        state=_text(row, "state"),
        status=_optional(row, "status"),
        waiting_for=_optional(row, "waitingFor"),
    )


def sessions(runner: git.Runner = git.run) -> Fleet:
    try:
        rows = json.loads(git.call(COMMAND, None, runner))
    except git.GitError as error:
        return Fleet([], f"the fleet read failed: {error}")
    except ValueError:
        return Fleet([], "the fleet read failed: claude agents printed no JSON")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return Fleet([], "the fleet read failed: claude agents printed no list of sessions")
    return Fleet([_session(row) for row in rows])


def hand_name(ref: str) -> str:
    return HAND + ref


def spike_name(key: str) -> str:
    return SPIKE + key


def session_of(ref: str) -> str:
    return spike_name(ref) if ref and ref == os.environ.get("DECKHAND_SPIKE") else hand_name(ref)


def ref_of(name: str) -> str:
    return name.removeprefix(SPIKE) if name.startswith(SPIKE) else name.removeprefix(HAND)


def _rank(session: Session) -> tuple[bool, str]:
    return session.status is not None, session.started_at


def _newest(sessions: list[Session], prefix: str) -> dict[str, Session]:
    found: dict[str, Session] = {}
    for session in sessions:
        if not session.name.startswith(prefix):
            continue
        name = session.name.removeprefix(prefix)
        if name not in found or _rank(session) > _rank(found[name]):
            found[name] = session
    return found


def hands(sessions: list[Session]) -> dict[str, Session]:
    return _newest(sessions, HAND)


def spikes(sessions: list[Session]) -> dict[str, Session]:
    return _newest(sessions, SPIKE)


def captain(sessions: list[Session]) -> Session | None:
    held = [s for s in sessions if s.name == CAPTAIN and s.status is not None]
    return max(held, key=lambda s: s.started_at, default=None)


def alive(session: Session) -> bool:
    return session.status is not None


def live(session: Session) -> bool:
    return session.status is not None and session.state in LIVE


def failed(session: Session) -> bool:
    return session.state in FAILED


def holds_slot(session: Session) -> bool:
    if not live(session):
        return False
    return session.state == "working" or session.waiting_for == PERMISSION


def slots_used(sessions: list[Session]) -> int:
    held = [*hands(sessions).values(), *spikes(sessions).values()]
    return sum(holds_slot(session) for session in held)
