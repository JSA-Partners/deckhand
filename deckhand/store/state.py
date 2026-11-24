from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from deckhand.records import Clock
from deckhand.store.local import connect, guard

STALE_AFTER = 30.0
# A wall-clock gap longer than this is a wake from sleep.
SLEEP_GAP = 30.0
HEARTBEAT = "heartbeat"


def alive(beat: float | None, now: float) -> bool:
    return beat is not None and now - beat <= STALE_AFTER


@dataclass(frozen=True)
class Message:
    seq: int
    key: str
    hand: str
    kind: str
    body: str
    written: str
    delivered: str | None
    acked: str | None


@dataclass(frozen=True)
class HandRow:
    name: str
    ref: str
    issue: str
    kind: str
    cwd: str
    wanted: str
    short: str | None
    session: str | None
    turn_ended: str | None
    restarts: int


_MESSAGE = "SELECT seq, key, hand, kind, body, written, delivered, acked FROM messages"
_HAND = "SELECT name, ref, issue, kind, cwd, wanted, short, session, turn_ended, restarts FROM hands"


class LocalState:
    def __init__(self, path: Path, now: Callable[[], str] | None = None) -> None:
        self._path = path
        self._now = now or Clock()
        self._db = connect(path)
        self._version: int | None = None

    def __enter__(self) -> LocalState:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with guard(self._path):
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def _read(self, sql: str, params: Sequence[object] = ()) -> list[tuple]:
        with guard(self._path):
            return self._db.execute(sql, params).fetchall()

    def post(self, key: str, hand: str, kind: str, body: str) -> bool:
        with self._write() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO messages (key, hand, kind, body, written) VALUES (?, ?, ?, ?, ?)",
                (key, hand, kind, body, self._now()),
            )
            return cursor.rowcount == 1

    def pending(self, hand: str | None = None) -> list[Message]:
        if hand is None:
            rows = self._read(f"{_MESSAGE} WHERE acked IS NULL ORDER BY seq")
        else:
            rows = self._read(f"{_MESSAGE} WHERE acked IS NULL AND hand = ? ORDER BY seq", (hand,))
        return [Message(*row) for row in rows]

    def delivered(self, seqs: Sequence[int]) -> None:
        if not seqs:
            return
        marks = ", ".join("?" * len(seqs))
        with self._write() as db:
            db.execute(
                f"UPDATE messages SET delivered = ? WHERE delivered IS NULL AND seq IN ({marks})",
                (self._now(), *seqs),
            )

    def ack(self, hand: str, through: int) -> int:
        at = self._now()
        with self._write() as db:
            cursor = db.execute(
                "UPDATE messages SET acked = ?, delivered = COALESCE(delivered, ?) "
                "WHERE acked IS NULL AND hand = ? AND seq <= ?",
                (at, at, hand, through),
            )
            return cursor.rowcount

    def want(self, name: str, ref: str, issue: str, kind: str, cwd: str) -> None:
        with self._write() as db:
            found = db.execute("SELECT kind FROM hands WHERE name = ?", (name,)).fetchone()
            if found is None:
                db.execute(
                    "INSERT INTO hands (name, ref, issue, kind, cwd, wanted) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, ref, issue, kind, cwd, self._now()),
                )
            elif found[0] == kind:
                db.execute("UPDATE hands SET cwd = ? WHERE name = ?", (cwd, name))
            else:
                db.execute(
                    "UPDATE hands SET ref = ?, issue = ?, kind = ?, cwd = ?, wanted = ?, short = NULL, "
                    "session = NULL, turn_ended = NULL, restarts = 0 WHERE name = ?",
                    (ref, issue, kind, cwd, self._now(), name),
                )

    def _update(self, sql: str, params: Sequence[object]) -> None:
        with self._write() as db:
            db.execute(sql, params)

    def launched(self, name: str, short: str) -> None:
        self._update("UPDATE hands SET short = ?, turn_ended = NULL WHERE name = ?", (short, name))

    def restarted(self, name: str) -> None:
        self._update("UPDATE hands SET restarts = restarts + 1 WHERE name = ?", (name,))

    def adopt(self, name: str, short: str, session: str) -> None:
        self._update("UPDATE hands SET short = ?, session = ? WHERE name = ?", (short, session, name))

    def turn_ended(self, name: str) -> None:
        self._update("UPDATE hands SET turn_ended = ? WHERE name = ?", (self._now(), name))

    def hands(self) -> dict[str, HandRow]:
        return {row[0]: HandRow(*row) for row in self._read(f"{_HAND} ORDER BY name")}

    def put(self, key: str, body: dict) -> None:
        self._update(
            "INSERT OR REPLACE INTO engine (key, body, written) VALUES (?, ?, ?)",
            (key, json.dumps(body, sort_keys=True), self._now()),
        )

    def get(self, key: str) -> dict | None:
        rows = self._read("SELECT body FROM engine WHERE key = ?", (key,))
        return json.loads(rows[0][0]) if rows else None

    def drop(self, key: str) -> None:
        self._update("DELETE FROM engine WHERE key = ?", (key,))

    def beat(self, pid: int, at: float) -> None:
        self.put(HEARTBEAT, {"pid": pid, "at": at})

    def heartbeat(self) -> tuple[int, float] | None:
        body = self.get(HEARTBEAT)
        if not isinstance(body, dict):
            return None
        pid, at = body.get("pid"), body.get("at")
        if not isinstance(pid, int) or isinstance(pid, bool) or isinstance(at, bool) or not isinstance(at, int | float):
            return None
        return pid, float(at)

    def changed(self) -> bool:
        version = self._read("PRAGMA data_version")[0][0]
        seen, self._version = self._version, version
        return version != seen
