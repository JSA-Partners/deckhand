from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from deckhand import shown
from tests.shell import row
from tests.test_reconcile import alone, facts, held, mail
from tests.test_step_hand import clone, state

__all__ = ["clone", "state"]

NAME = "hand-ACME-1"


def test_a_name_busy_with_no_launch_resume_or_stop_is_neither_quiet_nor_counted(clone: Path):
    store = alone()
    found = replace(facts(store, row("ACME-1", "idle", "idle"), hands=(held(),)), busy=frozenset({NAME}))
    assert shown.held(found, frozenset()) == (frozenset(), 0, frozenset())


def test_a_reserved_hand_whose_resume_is_in_flight_counts_once_and_stays_resuming(clone: Path):
    store = alone()
    found = facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(mail(),), unheard=frozenset({1}))
    assert shown.held(found, frozenset({NAME})) == (frozenset({"ACME-1"}), 1, frozenset({NAME}))
    assert shown.held(found, frozenset()) == (frozenset({"ACME-1"}), 1, frozenset({NAME}))
