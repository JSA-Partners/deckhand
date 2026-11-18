from __future__ import annotations

from datetime import date

import pytest

from deckhand.features import Budget, Feature, Phase, open_feature, phase
from deckhand.ops import create_id
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.store.fake import FakeStore

APPROVAL = Record("feature-approval", "s", "1", "me", {})


def story(id: str, state: str = "Backlog") -> dict:
    return {"id": id, "state": state}


def project(start: str | None = "2026-11-16") -> dict:
    return {"id": "p", "name": "Feature", "description": "", "start_date": start, "target_date": None}


def test_phase_parked_planning_building_released():
    assert phase(Feature(project(None))) is Phase.PARKED
    assert phase(Feature(project(), [story("a")])) is Phase.PLANNING
    assert phase(Feature(project())) is Phase.PLANNING
    approved = {"a": [APPROVAL], "b": [APPROVAL]}
    assert phase(Feature(project(), [story("a"), story("b")], approved, "1")) is Phase.BUILDING
    assert phase(Feature(project(), [story("a"), story("b")], approved, "2")) is Phase.PLANNING
    assert phase(Feature(project(), [story("a"), story("b")], {"a": [APPROVAL]})) is Phase.PLANNING
    done = [story("a", "Done"), story("b", "Canceled"), story("c", "Duplicate")]
    assert phase(Feature(project(), done)) is Phase.RELEASED


def test_open_feature_refuses_two():
    store = FakeStore()
    for name in ("Alpha", "Beta"):
        id = create_id(f"ACME:{name}", "feature")
        store.create_project(id, "ACME", name, "")
        store.set_project_dates(id, "2026-11-16", "2026-11-23")
    with pytest.raises(Refusal, match=r"(?=.*Alpha)(?=.*Beta)"):
        open_feature(store, "ACME")


def test_budget_note_after_half_of_one_week():
    start = date(2026, 11, 16)
    assert not Budget(1, start, date(2026, 11, 19)).planning_note
    assert Budget(1, start, date(2026, 11, 20)).planning_note


def test_budget_over_after_target():
    start = date(2026, 11, 16)
    assert not Budget(2, start, date(2026, 11, 30)).over
    assert Budget(2, start, date(2026, 12, 1)).over
    assert Budget(2, start, start).budget_days == 14


def test_building_ignores_canceled_and_duplicate_stories():
    stories = [story("a"), story("b", "Canceled"), story("c", "Duplicate")]
    assert phase(Feature(project(), stories, {"a": [APPROVAL]}, "1")) is Phase.BUILDING
    assert phase(Feature(project(), [*stories[1:], story("d")], {})) is Phase.PLANNING
    assert phase(Feature(project(), stories, {})) is Phase.PLANNING
