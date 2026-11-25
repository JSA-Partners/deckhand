from __future__ import annotations

from datetime import date

import pytest

from deckhand import reading
from deckhand.config import LocalConfig
from deckhand.fleet import Fleet
from deckhand.linear.client import LinearError
from deckhand.step import Refusal
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import approve_everything, setup
from tests.test_queue import ME, Clock, alpha, hand, started

DAY = date(2026, 11, 17)
LOCAL = LocalConfig(slot_cap=1)
MISSING = "missing credentials file: /config/linear-key"


def building() -> FakeStore:
    store = setup(FakeStore(now=Clock()))
    approve_everything(store)
    return store


def read(
    store: FakeStore,
    person=None,
    sessions: tuple = (),
    quiet: frozenset[str] = frozenset(),
    restarts: dict[str, int] | None = None,
) -> reading.Reading:
    person = person or (lambda: store)
    return reading.read(store, person, "ACME", Fleet(list(sessions)), LOCAL, DAY, quiet, restarts=restarts)


def story(ref: str, title: str, since: str) -> dict:
    stage = {"name": "planning", "round": None, "since": since}
    found = {"ref": ref, "title": title, "state": "Backlog", "owner": None, "mine": True, "blocked_by": []}
    return found | {"stage": stage, "completed_at": None}


def test_read_returns_the_building_facts():
    found = read(building())
    assert (found.team, found.me, found.waiting, found.notes) == ("ACME", ME, [], [])
    assert found.feature is not None and found.feature.project["name"] == "Alpha"
    assert found.facts == {
        "notes": [],
        "unreachable": False,
        "top": None,
        "slots": {"used": 0, "cap": 1},
        "feature": "Alpha",
        "budget": {"feature": "Alpha", "days_used": 1, "budget_days": 7, "planning_note": False},
        "order": [
            story("ACME-1", "Orders", "2026-11-18T11:06:56.000Z"),
            story("ACME-2", "Receipts", "2026-11-18T11:06:57.000Z"),
        ],
        "cycle": [],
        "release": False,
    }


def test_read_without_a_personal_key_has_no_queue_and_says_why():
    def missing():
        raise Refusal(MISSING)

    found = read(building(), person=missing)
    assert (found.me, found.waiting) == (None, [])
    assert found.notes == [MISSING]
    assert found.facts["notes"] == [MISSING]


def test_read_lets_a_linear_error_through():
    def unreachable():
        raise LinearError("network", "could not reach Linear")

    with pytest.raises(LinearError):
        read(building(), person=unreachable)


def test_a_quiet_story_gets_no_stalled_item():
    store = alpha()
    started(store, "s1")
    gone = (hand("ACME-1", "failed", None),)
    assert [(i.kind, i.ref) for i in read(store, sessions=gone).waiting] == [("stalled", "ACME-1")]
    assert read(store, sessions=gone, quiet=frozenset({"ACME-1"})).waiting == []


def test_read_passes_the_restart_counts_to_the_queue():
    store = alpha()
    started(store, "s1")
    gone = (hand("ACME-1", "failed", None),)
    assert [(i.kind, i.ref) for i in read(store, sessions=gone, restarts={"ACME-1": 1}).waiting] == [
        ("stalled", "ACME-1")
    ]
    assert read(store, sessions=gone, restarts={"ACME-1": 0}).waiting == []


def test_missing_clone_degrades_to_a_note():
    notes: list[str] = []
    policy = reading.policy_for(notes)({"labels": ["widgets"]})
    assert policy.second_signoff is False
    assert [n.startswith("no clone for widgets in ") for n in notes] == [True]
