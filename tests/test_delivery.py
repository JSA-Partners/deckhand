from __future__ import annotations

from pathlib import Path

from deckhand import queue, wakes
from deckhand.actions import Resume, Stop
from deckhand.reconcile import reconcile
from deckhand.records import HAND, Record
from deckhand.steps.after import After
from deckhand.store.state import Message
from tests.shell import row
from tests.test_chores import opened_next, released
from tests.test_feature_approval import TEAM, setup
from tests.test_reconcile import T, alone, at, clone, facts, hand_row, held, launch, mail, resume, started, state
from tests.test_step_hand import ME, building

__all__ = ["clone", "state"]


def test_a_stopped_hand_with_mail_is_resumed_with_the_text(clone: Path):
    store = alone()
    m = mail()
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(m,)), store)
    assert found == [resume(store, [m])]


def test_a_stopped_hand_with_mail_waits_for_a_slot(clone: Path):
    store = alone()
    sessions = (row("ACME-1", "stopped"), row("ACME-9", "working", "busy"))
    assert reconcile(facts(store, *sessions, hands=(held(),), pending=(mail(),)), store) == []


def test_an_alive_hand_with_fresh_mail_is_left_to_its_watcher(clone: Path):
    store = alone()
    idle = row("ACME-1", "idle", "idle")
    assert reconcile(facts(store, idle, hands=(held(),), pending=(mail(),), now=T + 10), store) == []


def test_an_idle_alive_hand_deaf_for_two_minutes_is_stopped(clone: Path):
    store = alone()
    idle = row("ACME-1", "idle", "idle")
    found = reconcile(facts(store, idle, hands=(held(),), pending=(mail(),), now=T + 121), store)
    assert found == [Stop("hand-ACME-1", "id-ACME-1", "unheard", (1,))]
    seen = mail(delivered=at(T + 5))
    assert reconcile(facts(store, idle, hands=(held(),), pending=(seen,), now=T + 121), store) == []


def test_a_working_hand_or_one_at_a_native_prompt_is_never_stopped_as_deaf(clone: Path):
    store = alone()
    for session in (row("ACME-1", "working", "busy"), row("ACME-1", "idle", "idle") | {"waitingFor": "input needed"}):
        assert reconcile(facts(store, session, hands=(held(),), pending=(mail(),), now=T + 600), store) == []


def test_a_due_story_with_unacknowledged_mail_is_not_nudged(clone: Path):
    store = alone()
    started(store)
    seen = mail(delivered=at(T))
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(seen,)), store)
    assert [type(action) for action in found] == [Resume]


def test_a_resume_carries_mail_delivered_but_not_acknowledged(clone: Path):
    store = alone()
    started(store)
    seen, fresh = mail(1, delivered=at(T)), mail(2)
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(seen, fresh)), store)
    assert found == [resume(store, [seen, fresh])]


def test_mail_for_a_stopped_hand_wins_the_last_slot_over_a_new_story(clone: Path):
    store = building()
    m = mail()
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(m,), cap=1), store)
    assert found == [resume(store, [m])]
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(m,), cap=2), store)
    assert found == [resume(store, [m]), launch(store, clone, "ACME-2")]


def test_a_wake_from_sleep_restarts_the_deaf_clock(clone: Path):
    store = alone()
    idle = row("ACME-1", "idle", "idle")
    woken = facts(store, idle, hands=(held(),), pending=(mail(),), woke=T + 3600, now=T + 3610)
    assert reconcile(woken, store) == []


def test_a_draft_session_is_never_lent_to_a_story_hand_with_mail(clone: Path):
    store = alone()
    started(store, kind=queue.DRAFT_HAND)
    found = reconcile(facts(store, row("ACME-1", "done"), hands=(hand_row(),), pending=(mail(),)), store)
    assert found == [launch(store, clone)]


def test_a_finished_hand_with_mail_is_never_resumed(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    store.set_issue_state("s1", "Done")
    assert reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(mail(),)), store) == []


def test_a_written_draft_with_mail_is_never_resumed(clone: Path):
    store = setup()
    store.assign("s2", "someone-else")
    started(store, kind=queue.DRAFT_HAND)
    store.put_record("s1", Record(queue.DRAFT, "s1", "v1", HAND, {}))
    hand = held(kind=queue.DRAFT_HAND)
    assert reconcile(facts(store, row("ACME-1", "stopped"), hands=(hand,), pending=(mail(),)), store) == []


def test_an_unheard_stop_and_its_resume_are_not_the_stops_restart(clone: Path):
    store = alone()
    started(store)
    sync = Message(1, "sync:s2:hand-ACME-1", "hand-ACME-1", wakes.SYNC, "ACME-2 merged.", at(T), None, None)
    hand, heard = held(restarts=1), frozenset({1})
    idle, stopped = row("ACME-1", "idle", "idle"), row("ACME-1", "stopped")
    found = reconcile(facts(store, idle, hands=(hand,), pending=(sync,), now=T + 121), store)
    assert found == [Stop("hand-ACME-1", "id-ACME-1", "unheard", (1,))]
    found = reconcile(facts(store, stopped, hands=(hand,), pending=(sync,), now=T + 130, unheard=heard), store)
    assert found == [Resume("hand-ACME-1", "sid-ACME-1", "/w/acme-1", wakes.prompt([sync]), (1,))]
    assert reconcile(facts(store, idle, hands=(hand,), pending=(sync,), now=T + 600, unheard=heard), store) == []


def test_the_deaf_clock_runs_from_the_last_turn_end(clone: Path):
    store = alone()
    idle = row("ACME-1", "idle", "idle")
    hand = held(turn_ended=at(T + 100))
    assert reconcile(facts(store, idle, hands=(hand,), pending=(mail(),), now=T + 150), store) == []
    found = reconcile(facts(store, idle, hands=(hand,), pending=(mail(),), now=T + 221), store)
    assert found == [Stop("hand-ACME-1", "id-ACME-1", "unheard", (1,))]


def test_an_unheard_stop_waits_for_a_slot_to_resume_into(clone: Path):
    store = alone()
    started(store)
    sync = Message(1, "sync:s3:hand-ACME-1", "hand-ACME-1", wakes.SYNC, "ACME-3 merged.", at(T), None, None)
    sessions = (row("ACME-1", "idle", "idle"), row("ACME-9", "working", "busy"))
    assert reconcile(facts(store, *sessions, hands=(held(),), pending=(sync,), now=T + 121), store) == []


def test_a_hand_that_left_its_chore_to_the_person_is_not_resumed_in_a_released_feature(clone: Path):
    store = released(["rotate the webhook secret"])
    opened_next(store)
    After(store).apply(["ACME-1", "person", "1", "--why", "the secret lives in the vault", *TEAM])
    assert reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(mail(),)), store) == []
