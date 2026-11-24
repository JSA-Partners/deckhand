from __future__ import annotations

from pathlib import Path

from deckhand import budget
from deckhand.store.state import LocalState


def taken(at: float, remaining: int, costs: dict[str, int]) -> dict:
    return {"budget": {"limit": 100, "remaining": remaining, "reset": None, "at": at, "until": None}, "costs": costs}


def test_nothing_kept_reads_as_an_empty_budget(tmp_path: Path) -> None:
    with LocalState(tmp_path / "deckhand.db") as rows:
        assert budget.kept(rows) == budget.Budget()


def test_keep_takes_the_newer_budget_and_merges_the_costs(tmp_path: Path) -> None:
    with LocalState(tmp_path / "deckhand.db") as rows:
        budget.keep(rows, "app", taken(20.0, 50, {"Issue": 4}))
        budget.keep(rows, "app", taken(10.0, 90, {"Viewer": 1}))
        assert budget.kept(rows) == budget.Budget(limit=100, remaining=50)
        assert rows.get(budget.KEY)["app"]["costs"] == {"Issue": 4, "Viewer": 1}


def test_each_role_keeps_its_own_budget(tmp_path: Path) -> None:
    with LocalState(tmp_path / "deckhand.db") as rows:
        budget.keep(rows, "app", taken(10.0, 50, {}))
        budget.keep(rows, "person", taken(10.0, 7, {}))
        assert (budget.kept(rows, "app").remaining, budget.kept(rows, "person").remaining) == (50, 7)


def test_a_kept_budget_of_another_shape_reads_as_empty(tmp_path: Path) -> None:
    with LocalState(tmp_path / "deckhand.db") as rows:
        rows.put(budget.KEY, {"app": {"budget": "x"}})
        assert budget.kept(rows) == budget.Budget()
