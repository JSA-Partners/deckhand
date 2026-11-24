from __future__ import annotations

from pathlib import Path

import pytest

from deckhand.records import Record
from deckhand.store import open as store_open


def test_open_store_keeps_records_in_the_state_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    roles: list[str] = []
    monkeypatch.setattr(store_open.Credentials, "load", staticmethod(lambda folder: "credentials"))
    monkeypatch.setattr(store_open, "connect", lambda credentials, role: roles.append(role) or object())
    uuid = "3f2b8c9e-7a41-4d55-9c1e-0a1b2c3d4e5f"
    store = store_open.open_store("app")
    store.put_record(uuid, Record("approval", "plan", "v1", "captain", {"ok": True}))
    assert roles == ["app"]
    assert (tmp_path / "deckhand.db").exists()
    assert [r.subject for r in store.records(uuid)] == ["plan"]
