from __future__ import annotations

from pathlib import Path

import pytest

from deckhand import budget, config
from deckhand.records import Record
from deckhand.store import open as store_open
from deckhand.store.state import LocalState
from tests.fake_graphql import FakeGraphQL, team_id
from tests.test_step_stories import setup as feature_store


def test_open_store_keeps_records_in_the_state_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    roles: list[str] = []
    monkeypatch.setattr(store_open.Credentials, "load", staticmethod(lambda folder: "credentials"))
    monkeypatch.setattr(store_open, "connect", lambda credentials, role, meter: roles.append(role) or object())
    uuid = "3f2b8c9e-7a41-4d55-9c1e-0a1b2c3d4e5f"
    store = store_open.open_store("app")
    store.put_record(uuid, Record("approval", "plan", "v1", "captain", {"ok": True}))
    assert roles == ["app"]
    assert (tmp_path / "deckhand.db").exists()
    assert [r.subject for r in store.records(uuid)] == ["plan"]


def test_open_store_meters_each_role_on_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    meters: dict[str, object] = {}
    monkeypatch.setattr(store_open.Credentials, "load", staticmethod(lambda folder: "credentials"))
    monkeypatch.setattr(store_open, "connect", lambda credentials, role, meter: meters.setdefault(role, meter))
    store_open.open_store("app")
    store_open.open_store("person")
    assert meters == {"app": store_open.METERS["app"], "person": store_open.METERS["person"]}


def test_flush_keeps_what_each_meter_heard_in_the_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(store_open, "METERS", {"app": store_open.Meter(lambda: 100.0), "person": store_open.Meter()})
    headers = {"X-Complexity": "12", "X-RateLimit-Complexity-Limit": "2000000"}
    store_open.METERS["app"].heard("ProjectIssues", headers | {"X-RateLimit-Complexity-Remaining": "900"}, False)
    store_open.flush()
    with LocalState(tmp_path / "deckhand.db") as rows:
        assert budget.kept(rows, "app") == budget.Budget(limit=2000000, remaining=900)
        assert rows.get(budget.KEY)["app"]["costs"] == {"ProjectIssues": 12}
        assert "person" not in rows.get(budget.KEY)


def test_flush_with_nothing_heard_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(store_open, "METERS", {"app": store_open.Meter(), "person": store_open.Meter()})
    store_open.flush()
    assert not (tmp_path / "deckhand.db").exists()


def test_flush_into_a_broken_state_stays_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    (tmp_path / "deckhand.db").write_text("not a database")
    monkeypatch.setattr(store_open, "METERS", {"app": store_open.Meter(), "person": store_open.Meter()})
    store_open.METERS["app"].heard("Viewer", {"X-Complexity": "1"}, False)
    store_open.flush()


def test_a_configured_team_id_saves_the_team_query(monkeypatch: pytest.MonkeyPatch) -> None:
    directory = config.config_dir()
    directory.mkdir(parents=True)
    (directory / config.FILE).write_text(f'team_key = "ACME"\n\n[teams]\nACME = "{team_id("ACME")}"\n')
    client = FakeGraphQL(feature_store())
    monkeypatch.setattr(store_open.Credentials, "load", staticmethod(lambda folder: "credentials"))
    monkeypatch.setattr(store_open, "connect", lambda credentials, role, meter: client)
    [found] = store_open.open_store("app").team_projects("ACME")
    assert found["name"] == "Alpha"
    assert client.names == ["TeamProjects"]


def test_a_passed_team_dict_learns_the_configured_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    directory = config.config_dir()
    directory.mkdir(parents=True)
    (directory / config.FILE).write_text('[teams]\nACME = "team-ACME"\n')
    monkeypatch.setattr(store_open.Credentials, "load", staticmethod(lambda folder: "credentials"))
    monkeypatch.setattr(store_open, "connect", lambda credentials, role, meter: object())
    kept = {"OTHER": "team-OTHER"}
    store_open.open_store("app", kept)
    assert kept == {"OTHER": "team-OTHER", "ACME": "team-ACME"}


def test_a_stale_configured_team_id_still_reads_the_team(monkeypatch: pytest.MonkeyPatch) -> None:
    directory = config.config_dir()
    directory.mkdir(parents=True)
    (directory / config.FILE).write_text('team_key = "ACME"\n\n[teams]\nACME = "stale-team-id"\n')
    client = FakeGraphQL(feature_store())
    monkeypatch.setattr(store_open.Credentials, "load", staticmethod(lambda folder: "credentials"))
    monkeypatch.setattr(store_open, "connect", lambda credentials, role, meter: client)
    [found] = store_open.open_store("app").team_projects("ACME")
    assert found["name"] == "Alpha"
    assert client.names == ["TeamProjects", "TeamId", "TeamProjects"]
