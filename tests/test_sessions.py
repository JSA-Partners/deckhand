"""Every session working on a repository the board holds, read from the tail of its transcript."""

from __future__ import annotations

import json
from pathlib import Path

from deckhand import sessions


def _write(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def test_records_come_back_newest_first(tmp_path):
    path = _write(tmp_path / "s.jsonl", [{"n": 1}, {"n": 2}, {"n": 3}])
    assert [record["n"] for record in sessions.records_back(path)] == [3, 2, 1]


def test_a_blank_line_is_not_a_record(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"n": 1}\n\n{"n": 2}\n', encoding="utf-8")
    assert [record["n"] for record in sessions.records_back(path)] == [2, 1]


def test_a_record_longer_than_a_chunk_is_read_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHUNK", 8)
    path = _write(tmp_path / "s.jsonl", [{"n": 1, "pad": "x" * 200}, {"n": 2}])
    assert [record["n"] for record in sessions.records_back(path)] == [2, 1]
