from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from deckhand import answers
from deckhand.records import Clock, Record
from deckhand.ref import Ref
from deckhand.step import Refusal

SCHEMA_VERSION = 2
BUSY_SECONDS = 5

_PROBLEMS = {
    sqlite3.SQLITE_BUSY: "is locked by another process",
    sqlite3.SQLITE_LOCKED: "is locked by another process",
    sqlite3.SQLITE_CANTOPEN: "cannot be opened",
    sqlite3.SQLITE_READONLY: "is read-only",
    sqlite3.SQLITE_CORRUPT: "is corrupt",
    sqlite3.SQLITE_NOTADB: "is corrupt",
}

_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS records (
        issue TEXT NOT NULL,
        kind TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        written TEXT NOT NULL,
        PRIMARY KEY (issue, kind, subject)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE NOT NULL,
        hand TEXT NOT NULL,
        kind TEXT NOT NULL,
        body TEXT NOT NULL,
        written TEXT NOT NULL,
        delivered TEXT,
        acked TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hands (
        name TEXT PRIMARY KEY,
        ref TEXT NOT NULL,
        issue TEXT NOT NULL,
        kind TEXT NOT NULL,
        cwd TEXT NOT NULL,
        wanted TEXT NOT NULL,
        short TEXT,
        session TEXT,
        turn_ended TEXT,
        restarts INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS engine (
        key TEXT PRIMARY KEY,
        body TEXT NOT NULL,
        written TEXT NOT NULL
    )
    """,
)

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
        connect(path).close()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = connect(self._path)
        try:
            with guard(self._path):
                yield db
        finally:
            db.close()

    def put_record(self, issue: str, record: Record) -> None:
        require_id(issue)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(_UPSERT, (issue, record.kind, record.subject, record.to_json(), self._now()))
            db.execute("COMMIT")

    def records(self, issue: str) -> list[Record]:
        require_id(issue)
        with self._connection() as db:
            rows = db.execute(
                "SELECT body, written FROM records WHERE issue = ? ORDER BY kind || '/' || subject",
                (issue,),
            ).fetchall()
        return [replace(Record.from_json(body), at=written) for body, written in rows]


def connect(path: Path) -> sqlite3.Connection:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise _refusal(path, "cannot be opened") from None
    with guard(path):
        db = sqlite3.connect(path, timeout=BUSY_SECONDS, isolation_level=None)
        try:
            _prepare(db)
        except BaseException:
            db.close()
            raise
        return db


@contextmanager
def guard(path: Path) -> Iterator[None]:
    try:
        yield
    except sqlite3.Error as error:
        raise _refusal(path, _PROBLEMS.get(_code(error), "cannot be used")) from None


def _refusal(path: Path, problem: str) -> Refusal:
    return Refusal(f"{path.name} in the state folder {path.parent} {problem}")


def _code(error: sqlite3.Error) -> int:
    return getattr(error, "sqlite_errorcode", 0) & 0xFF


def _prepare(db: sqlite3.Connection) -> None:
    deadline = time.monotonic() + BUSY_SECONDS
    while True:
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                _create(db)
            return
        except sqlite3.OperationalError as error:
            if _code(error) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or time.monotonic() >= deadline:
                raise
            if db.in_transaction:
                db.execute("ROLLBACK")
            time.sleep(0.01)


def _create(db: sqlite3.Connection) -> None:
    # The switch to WAL ignores the busy timeout, so wait for the write lock first.
    db.execute("BEGIN IMMEDIATE")
    db.execute("COMMIT")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("BEGIN IMMEDIATE")
    if db.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        for table in _TABLES:
            db.execute(table)
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    db.execute("COMMIT")


def require_id(issue: str) -> None:
    try:
        Ref.parse(issue)
    except ValueError:
        return
    raise Refusal(f"records are keyed by the issue id, not the identifier {issue}")
