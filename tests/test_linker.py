from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from deckhand import budget, config, linker
from deckhand.fleet import Fleet
from deckhand.step import Refusal
from deckhand.store import open as store_open
from deckhand.store.cached import CachedStore
from deckhand.store.fake import FakeStore
from deckhand.store.open import Stores
from tests.conftest import CountingStore, Wire

DAY = date(2026, 11, 17)
T = 1_763_546_400.0


def linked(store: CountingStore, at: float, kept: linker.Kept) -> linker.Linked:
    return linker.link("ACME", Fleet, lambda: at, lambda: Stores(app=store, person=store), lambda: DAY, kept)


def test_a_link_within_ten_minutes_reuses_the_repository_labels() -> None:
    store, kept = CountingStore(FakeStore()), linker.Kept()
    for at in (T, T + 60, T + linker.REFRESH - 1):
        assert linked(store, at, kept).error == ""
    assert store.calls["repository_labels"] == 1
    linked(store, T + linker.REFRESH, kept)
    assert store.calls["repository_labels"] == 2


def test_a_config_that_does_not_load_marks_the_link_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, kept = CountingStore(FakeStore()), linker.Kept()
    monkeypatch.setattr(linker.config, "local", lambda: config.LocalConfig(clones={"widgets": tmp_path}))
    assert linked(store, T, kept).configured

    def broken() -> config.LocalConfig:
        raise Refusal("config.toml: unknown key bogus")

    monkeypatch.setattr(linker.config, "local", broken)
    found = linked(store, T, kept)
    assert not found.configured and "config.toml: unknown key bogus" in found.problems


def test_a_config_with_no_clones_marks_the_link_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linker.config, "local", config.LocalConfig)
    found = linked(CountingStore(FakeStore()), T, linker.Kept())
    assert not found.configured
    assert any(line.startswith("no clone is configured in ") and line.endswith("run setup") for line in found.problems)


def test_links_share_one_memo_of_team_ids_until_it_is_refreshed(monkeypatch: pytest.MonkeyPatch) -> None:
    given: list[dict] = []

    def open_store(role: str, teams: dict[str, str] | None = None) -> CachedStore:
        given.append(teams)
        return CachedStore(FakeStore())

    monkeypatch.setattr(store_open, "open_store", open_store)
    kept = linker.Kept()
    for at in (T, T + 60, T + linker.REFRESH):
        linker.link("ACME", Fleet, lambda at=at: at, today=lambda: DAY, kept=kept)
    assert given[0] is given[1] is given[2] is given[3]
    assert given[4] is not given[0]


class Links:
    def __init__(self) -> None:
        self.made: list[linker.Linked] = []

    def __call__(self, team: str, fleet_now, clock, stores=None, today=None, kept=None) -> linker.Linked:
        self.made.append(linker.Linked(None, None, clock(), ""))
        return self.made[-1]


def gated(monkeypatch: pytest.MonkeyPatch, found: budget.Budget) -> tuple[linker.Linker, Links, list[float]]:
    links, now = Links(), [T]
    monkeypatch.setattr(linker, "link", links)
    monkeypatch.setattr(budget, "current", lambda: found)
    return linker.Linker("ACME", Fleet, lambda: now[0]), links, now


def test_a_link_with_budget_to_spare_reads_every_time(monkeypatch: pytest.MonkeyPatch) -> None:
    linked_now, links, now = gated(monkeypatch, budget.Budget(limit=100, remaining=90))
    for step in range(3):
        now[0] = T + step
        assert linked_now() is links.made[-1]
    assert len(links.made) == 3


def test_a_limited_budget_keeps_the_last_link_until_it_lifts(monkeypatch: pytest.MonkeyPatch) -> None:
    linked_now, links, now = gated(monkeypatch, budget.Budget(until=T + 600))
    first = linked_now()
    now[0] = T + 60
    assert linked_now() is first and len(links.made) == 1
    now[0] = T + 600
    assert linked_now() is not first and len(links.made) == 2


def test_a_low_budget_reads_every_five_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    linked_now, links, now = gated(monkeypatch, budget.Budget(limit=100, remaining=10, reset=T + 3600))
    first = linked_now()
    now[0] = T + budget.LOW_EVERY - 1
    assert linked_now() is first
    now[0] = T + budget.LOW_EVERY
    assert linked_now() is not first and len(links.made) == 2


def test_a_link_keeps_what_the_meters_heard(monkeypatch: pytest.MonkeyPatch) -> None:
    flushed: list[bool] = []
    linked_now, _, _ = gated(monkeypatch, budget.Budget())
    monkeypatch.setattr(store_open, "flush", lambda: flushed.append(True))
    linked_now()
    assert flushed == [True]


def test_a_link_after_a_network_error_reads_again(monkeypatch: pytest.MonkeyPatch, wire: Wire) -> None:
    def link(team, fleet_now, clock, stores=None, today=None, kept=None) -> linker.Linked:
        return linker.Linked(None, None, clock(), "" if wire.send() else "network")

    now = [T]
    monkeypatch.setattr(linker, "link", link)
    monkeypatch.setattr(budget, "current", lambda: budget.Budget(limit=100, remaining=90))
    linked_now = linker.Linker("ACME", Fleet, lambda: now[0])
    assert linked_now().error == "network"
    wire.up, now[0] = True, T + 1
    assert linked_now().error == ""
    assert wire.sent == 2
