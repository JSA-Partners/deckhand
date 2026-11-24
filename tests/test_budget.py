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


NOW = 1_763_546_400.0


def test_an_empty_budget_is_neither_low_nor_limited_and_always_due() -> None:
    found = budget.Budget()
    assert (found.limited(NOW), found.low(NOW), budget.due(found, NOW, NOW - 1), budget.phrase(found, NOW)) == (
        False,
        False,
        True,
        "",
    )


def test_a_limited_budget_is_not_due_until_it_lifts() -> None:
    found = budget.Budget(limit=100, remaining=0, until=NOW + 600)
    assert found.limited(NOW) and not budget.due(found, NOW, NOW - 3600)
    assert budget.due(found, NOW + 600, NOW - 3600)
    assert budget.phrase(found, NOW) == f"Linear paused until {budget.clock(NOW + 600)}"


def test_under_a_quarter_left_is_low_and_due_every_five_minutes() -> None:
    found = budget.Budget(limit=100, remaining=24, reset=NOW + 1200)
    assert found.low(NOW)
    assert not budget.due(found, NOW, NOW - budget.LOW_EVERY + 1)
    assert budget.due(found, NOW, NOW - budget.LOW_EVERY)
    assert budget.phrase(found, NOW) == "Linear budget low"


def test_a_quarter_or_more_left_is_not_low() -> None:
    assert not budget.Budget(limit=100, remaining=25, reset=NOW + 1200).low(NOW)


def test_a_low_budget_past_its_reset_is_not_low() -> None:
    assert not budget.Budget(limit=100, remaining=5, reset=NOW - 1).low(NOW)
