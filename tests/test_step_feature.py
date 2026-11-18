from __future__ import annotations

import json
from datetime import date

import pytest

from deckhand.ops import create_id
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.feature import Feature
from deckhand.store.fake import FakeStore

TEAM = ["--team-key", "ACME"]
TODAY = date(2026, 11, 20)


def step(store: FakeStore) -> Feature:
    return Feature(store, lambda: TODAY)


def parked(store: FakeStore, id: str = "p1", name: str = "Alpha", weeks: int = 2) -> str:
    store.create_project(id, "ACME", name, f"Budget: {weeks} weeks.")
    return id


def test_pick_refuses_while_another_feature_is_open():
    store = FakeStore()
    parked(store, "p1", "Alpha")
    parked(store, "p2", "Beta")
    store.set_project_dates("p1", "2026-11-16", "2026-11-23")
    store.writes.clear()
    with pytest.raises(Refusal, match=r"Alpha \(p1\)"):
        step(store).apply(["pick", *TEAM, "--project", "p2"])
    assert store.writes == []


def test_pick_sets_dates_from_budget():
    store = FakeStore()
    parked(store, weeks=2)
    step(store).apply(["pick", *TEAM, "--project", "p1"])
    assert store.writes[-1] == ("set_project_dates", "p1", "2026-11-20", "2026-12-04")


@pytest.mark.parametrize("weeks", ["0", "3", "x", ""])
def test_weeks_other_than_1_or_2_refuses(weeks: str):
    store = FakeStore()
    with pytest.raises(Refusal, match="1 or 2"):
        step(store).apply(["new", *TEAM, "--name", "Alpha", "--weeks", weeks])
    assert store.writes == []


def test_reference_marked_as_reference_in_description():
    store = FakeStore()
    args = ["new", *TEAM, "--name", "Alpha", "--weeks", "1", "--reference", "ACME-4"]
    step(store).apply(args)
    step(store).apply(args)
    [made] = store.team_projects("ACME")
    assert made["id"] == create_id("ACME:Alpha", "feature")
    assert "Reference (not a decision):\n- ACME-4" in made["description"]
    assert made["start_date"] is None
    assert store.writes == [("create_project", made["id"])]


def test_park_refuses_with_an_approved_story():
    store = FakeStore()
    parked(store)
    step(store).apply(["pick", *TEAM, "--project", "p1"])
    store.create_issue("i1", "ACME", "t", "", "p1", [])
    store.put_record("i1", Record("feature-approval", "i1", "1", "me", {}))
    store.writes.clear()
    with pytest.raises(Refusal, match="feature-approval"):
        step(store).apply(["park", *TEAM, "--project", "p1"])
    assert store.writes == []


def test_park_clears_dates():
    store = FakeStore()
    parked(store)
    step(store).apply(["pick", *TEAM, "--project", "p1"])
    step(store).apply(["park", *TEAM, "--project", "p1"])
    assert store.writes[-1] == ("set_project_dates", "p1", None, None)


def test_context_json_has_phase_budget_and_parked(capsys: pytest.CaptureFixture[str]):
    store = FakeStore()
    parked(store, "p1", "Alpha", 1)
    parked(store, "p2", "Beta")
    step(store).apply(["pick", *TEAM, "--project", "p1"])
    capsys.readouterr()
    step(store).context([*TEAM, "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert facts["open"]["phase"] == "planning"
    assert facts["open"]["budget"] == {"days_used": 0, "budget_days": 7, "planning_note": False, "over": False}
    assert facts["parked"] == [{"id": "p2", "name": "Beta"}]
