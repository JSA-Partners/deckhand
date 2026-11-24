from __future__ import annotations

from datetime import date

import pytest

from deckhand import linker
from deckhand.fleet import Fleet
from deckhand.store import open as store_open
from deckhand.store.cached import CachedStore
from deckhand.store.fake import FakeStore
from deckhand.store.open import Stores
from tests.conftest import CountingStore

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
