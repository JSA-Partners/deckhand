from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from deckhand.features import (
    FEATURE_LABEL,
    Budget,
    Feature,
    Phase,
    current,
    is_feature,
    load,
    open_feature,
    phase,
    team_features,
)
from deckhand.ops import create_id
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.store.cached import CachedStore
from deckhand.store.fake import FakeStore
from tests.conftest import CountingStore

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
        store.create_project(id, "ACME", name, "", [FEATURE_LABEL])
        store.set_project_dates(id, "2026-11-16", "2026-11-23")
    with pytest.raises(Refusal, match=r"(?=.*Alpha)(?=.*Beta)"):
        open_feature(store, "ACME")


def test_current_is_the_open_feature_else_the_newest_released():
    store = FakeStore()
    assert current(store, "ACME") is None
    for n, (name, start) in enumerate((("Alpha", "2026-11-02"), ("Beta", "2026-11-09"), ("Gamma", None))):
        id = create_id(f"ACME:{name}", "feature")
        store.create_project(id, "ACME", name, "", [FEATURE_LABEL])
        store.set_project_dates(id, start, None)
        store.create_issue(f"s{n}", "ACME", name, "", id, [])
        if start:
            store.set_issue_state(f"s{n}", "Done")
    found = current(store, "ACME")
    assert found is not None and found.project["name"] == "Beta"
    store.set_project_dates(create_id("ACME:Gamma", "feature"), "2026-11-16", None)
    found = current(store, "ACME")
    assert found is not None and (found.project["name"], phase(found)) == ("Gamma", Phase.PLANNING)


def test_current_is_the_released_feature_with_the_latest_target_date():
    store = FakeStore()
    dates = {
        "Later": ("2026-11-02", "2026-11-20"),
        "Same": ("2026-11-02", "2026-11-20"),
        "Undated": ("2026-11-30", None),
    }
    for name, (start, target) in dates.items():
        id = create_id(f"ACME:{name}", "feature")
        store.create_project(id, "ACME", name, "", [FEATURE_LABEL])
        store.set_project_dates(id, start, target)
        store.create_issue(f"s-{name}", "ACME", name, "", id, [])
        store.set_issue_state(f"s-{name}", "Done")
    found = current(store, "ACME")
    later, same = create_id("ACME:Later", "feature"), create_id("ACME:Same", "feature")
    assert found is not None and found.project["id"] == max(later, same)


def test_renamed_project_stays_a_feature():
    store = FakeStore()
    id = create_id("ACME:Alpha", "feature")
    store.create_project(id, "ACME", "Alpha", "", [FEATURE_LABEL])
    store._projects[id]["name"] = "Alpha renamed in Linear"
    [found] = team_features(store, "ACME")
    assert found["id"] == id and is_feature(found)


def test_unlabeled_project_is_not_a_feature():
    store = FakeStore()
    store.create_project(create_id("ACME:Alpha", "feature"), "ACME", "Alpha", "", ["other"])
    assert team_features(store, "ACME") == []


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


def started_feature(store: FakeStore, name: str, states: list[str]) -> None:
    id = create_id(f"ACME:{name}", "feature")
    store.create_project(id, "ACME", name, "", [FEATURE_LABEL])
    store.set_project_dates(id, "2026-11-16", None)
    for n, state in enumerate(states):
        store.create_issue(f"{name}_{n}", "ACME", name, "", id, [])
        store.set_issue_state(f"{name}_{n}", state)


def test_a_released_feature_reads_only_its_issues(counting_store: Callable[[FakeStore], CountingStore]):
    fake = FakeStore()
    started_feature(fake, "Alpha", ["Done", "Done"])
    counting = counting_store(fake)
    store = CachedStore(counting)
    found = load(store, store.team_projects("ACME")[0])
    assert phase(found) is Phase.RELEASED
    assert counting.calls["project_issues"] == 1
    assert "project_documents" not in counting.calls


def test_current_with_three_released_and_one_open_feature_reads_each_project_once(
    counting_store: Callable[[FakeStore], CountingStore],
):
    fake = FakeStore()
    for name in ("Alpha", "Beta", "Gamma"):
        started_feature(fake, name, ["Done"])
    started_feature(fake, "Delta", ["Backlog"])
    counting = counting_store(fake)
    store = CachedStore(counting)
    found = current(store, "ACME")
    assert found is not None and found.project["name"] == "Delta"
    assert counting.calls["project_issues"] == 1
    assert counting.calls["project_documents"] == 1


def test_current_with_no_open_feature_loads_only_the_latest_released_one(
    counting_store: Callable[[FakeStore], CountingStore],
):
    fake = FakeStore()
    for name in ("Alpha", "Beta"):
        started_feature(fake, name, ["Done"])
    counting = counting_store(fake)
    found = current(CachedStore(counting), "ACME")
    assert found is not None and phase(found) is Phase.RELEASED
    assert counting.calls["project_issues"] == 1


def test_open_feature_loads_no_released_project(counting_store: Callable[[FakeStore], CountingStore]):
    fake = FakeStore()
    for name in ("Alpha", "Beta"):
        started_feature(fake, name, ["Done"])
    counting = counting_store(fake)
    assert open_feature(CachedStore(counting), "ACME") is None
    assert "project_issues" not in counting.calls


def test_phase_of_an_open_feature_is_unchanged(counting_store: Callable[[FakeStore], CountingStore]):
    fake = FakeStore()
    started_feature(fake, "Alpha", ["Backlog"])
    store = CachedStore(counting_store(fake))
    found = load(store, store.team_projects("ACME")[0])
    assert phase(found) is Phase.PLANNING
    assert found.version is not None
    story_id = found.stories[0]["id"]
    approved = Feature(
        found.project,
        found.stories,
        {story_id: [Record("feature-approval", story_id, found.version, "me", {})]},
        found.version,
    )
    assert phase(approved) is Phase.BUILDING
