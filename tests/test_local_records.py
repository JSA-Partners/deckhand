from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.store.local import SCHEMA_VERSION, LocalRecords, default_path

ID = "7c1f3a52-0b7e-4c1e-9a55-3f7a1d2b9e10"
OTHER = "0d4e8b16-5a2c-4f0e-8d3b-6c9a7e1f2b34"


def tier(subject: str = "s", version: str = "v1") -> Record:
    return Record("tier", subject, version, "person", {"level": 1})


def test_a_record_round_trips_with_its_write_time(tmp_path: Path) -> None:
    store = LocalRecords(tmp_path / "deckhand.db", now=lambda: "2026-01-01T00:00:00.000Z")
    store.put_record(ID, Record("tier", ID, "v1", "person", {"level": 1}))
    found = store.records(ID)
    assert found == [Record("tier", ID, "v1", "person", {"level": 1})]
    assert found[0].at == "2026-01-01T00:00:00.000Z"


def test_a_second_write_of_the_same_kind_and_subject_replaces_the_first(tmp_path: Path) -> None:
    store = LocalRecords(tmp_path / "deckhand.db")
    store.put_record(ID, tier(version="v1"))
    store.put_record(ID, tier(version="v2"))
    found = store.records(ID)
    assert [r.version for r in found] == ["v2"]


def test_records_come_back_in_url_order(tmp_path: Path) -> None:
    store = LocalRecords(tmp_path / "deckhand.db")
    for kind, subject in [("wake", "a"), ("tier", "b"), ("wake", "b"), ("tier", "a")]:
        store.put_record(ID, Record(kind, subject, "v1", "person", {}))
    order = [(r.kind, r.subject) for r in store.records(ID)]
    assert order == [("tier", "a"), ("tier", "b"), ("wake", "a"), ("wake", "b")]


def test_records_of_another_issue_are_not_returned(tmp_path: Path) -> None:
    store = LocalRecords(tmp_path / "deckhand.db")
    store.put_record(ID, tier())
    assert store.records(OTHER) == []


def test_two_connections_write_at_once_without_locking_errors(tmp_path: Path) -> None:
    path = tmp_path / "deckhand.db"
    stores = [LocalRecords(path), LocalRecords(path)]
    errors: list[Exception] = []

    def write(store: LocalRecords, start: int) -> None:
        try:
            for n in range(start, 200, 2):
                store.put_record(ID, tier(subject=f"s{n:03d}"))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=write, args=(store, i)) for i, store in enumerate(stores)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(LocalRecords(path).records(ID)) == 200


def test_a_second_process_reads_what_the_first_wrote(tmp_path: Path) -> None:
    path = tmp_path / "deckhand.db"
    LocalRecords(path).put_record(ID, tier())
    assert [r.subject for r in LocalRecords(path).records(ID)] == ["s"]


def test_an_identifier_instead_of_a_uuid_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "deckhand.db"
    store = LocalRecords(path)
    with pytest.raises(Refusal, match="SBX-17"):
        store.put_record("SBX-17", tier())
    assert store.records(ID) == []
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM records").fetchone() == (0,)


def test_the_file_is_created_with_the_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deckhand.db"
    LocalRecords(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert db.execute("PRAGMA journal_mode").fetchone() == ("wal",)


def test_the_default_path_follows_the_state_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    assert default_path() == tmp_path / "deckhand.db"


def test_records_refuses_an_identifier(tmp_path: Path) -> None:
    store = LocalRecords(tmp_path / "deckhand.db")
    with pytest.raises(Refusal):
        store.records("ACME-5")
