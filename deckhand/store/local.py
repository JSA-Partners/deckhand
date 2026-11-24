from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from deckhand import answers
from deckhand.records import Clock, Record
from deckhand.ref import Ref
from deckhand.step import Refusal

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    issue TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    written TEXT NOT NULL,
    PRIMARY KEY (issue, kind, subject)
)
"""

_UPSERT = """
INSERT INTO records (issue, kind, subject, body, written) VALUES (?, ?, ?, ?, ?)
ON CONFLICT (issue, kind, subject) DO UPDATE SET body = excluded.body, written = excluded.written
"""


def default_path() -> Path:
    return answers.state_dir() / "deckhand.db"


class LocalRecords:
    def __init__(self, path: Path, now: Callable[[], str] | None = None) -> None:
        self._path = path
        self._now = now or Clock()
        path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(_SCHEMA)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=5, isolation_level=None)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def put_record(self, issue: str, record: Record) -> None:
        require_id(issue)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(_UPSERT, (issue, record.kind, record.subject, record.to_json(), self._now()))
            db.execute("COMMIT")
        finally:
            db.close()

    def records(self, issue: str) -> list[Record]:
        require_id(issue)
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT body, written FROM records WHERE issue = ? ORDER BY kind || '/' || subject",
                (issue,),
            ).fetchall()
        finally:
            db.close()
        return [replace(Record.from_json(body), at=written) for body, written in rows]


def require_id(issue: str) -> None:
    try:
        Ref.parse(issue)
    except ValueError:
        return
    raise Refusal(f"records are keyed by the issue id, not the identifier {issue}")
