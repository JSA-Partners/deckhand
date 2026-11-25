from __future__ import annotations

from deckhand import asks, fleet, queue, wakes
from deckhand.idle import IDLE_AFTER, idle, nudged
from deckhand.records import iso
from deckhand.store.state import HandRow, Message
from tests.shell import Shell, row

T = 1795000000.0
NAME = "hand-ACME-1"


def at(seconds: float) -> str:
    return iso(int(seconds * 1000))


def hand(turn_ended: float | None = T) -> HandRow:
    ended = at(turn_ended) if turn_ended is not None else None
    return HandRow(NAME, "ACME-1", "s1", queue.STORY_HAND, "/w", at(0), "id-ACME-1", "sid-ACME-1", ended, 0)


def blocked(**changes: object) -> dict:
    return row("ACME-1", "blocked", "busy") | changes


def found(*sessions: dict, row_: HandRow | None = None, pending: tuple[Message, ...] = (), **when: float):
    read = fleet.sessions(Shell(*sessions))
    now, woke = when.get("now", T + IDLE_AFTER + 1), when.get("woke", 0.0)
    return idle(read, {NAME: row_ or hand()}, list(pending), now, woke)


def message(kind: str) -> Message:
    return Message(1, f"{kind}:1", NAME, kind, "body", at(T), None, None)


def test_a_blocked_hand_with_no_prompt_two_minutes_past_its_turn_end_is_idle() -> None:
    assert found(blocked()) == {NAME}


def test_not_idle_before_two_minutes_or_without_a_turn_end() -> None:
    assert found(blocked(), now=T + IDLE_AFTER) == frozenset()
    assert found(blocked(), row_=hand(None)) == frozenset()


def test_a_working_stopped_or_prompting_hand_is_not_idle() -> None:
    for session in (
        row("ACME-1", "working", "busy"),
        row("ACME-1", "stopped"),
        blocked(waitingFor="permission prompt"),
    ):
        assert found(session) == frozenset()


def test_a_story_hand_reported_working_after_a_turn_end_is_not_idle() -> None:
    assert found(row("ACME-1", "working", "busy", at=int((T - 30) * 1000))) == frozenset()


def test_mail_waiting_ends_idleness_but_its_own_nudge_does_not() -> None:
    assert found(blocked(), pending=(message(asks.ANSWER),)) == frozenset()
    assert found(blocked(), pending=(message(wakes.NUDGE),)) == {NAME}


def test_the_idle_clock_restarts_on_a_wake_from_sleep() -> None:
    assert found(blocked(), woke=T + 100, now=T + 150) == frozenset()


def test_a_failed_fleet_read_finds_no_idle_hand() -> None:
    assert idle(fleet.sessions(Shell(fleet_fails=True)), {NAME: hand()}, [], T + 600, 0.0) == frozenset()


def test_a_hand_is_nudged_while_its_nudge_is_unheard_inside_the_window() -> None:
    nudge = message(wakes.NUDGE)
    assert nudged([nudge], T + IDLE_AFTER, 0.0) == {NAME}
    assert nudged([nudge], T + IDLE_AFTER + 1, 0.0) == frozenset()
    assert nudged([message(asks.ANSWER)], T + 1, 0.0) == frozenset()
    assert nudged([Message(1, "k", NAME, wakes.NUDGE, "b", at(T), at(T + 5), None)], T + 10, 0.0) == frozenset()
