from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from deckhand import asks
from deckhand.records import HAND, Record
from deckhand.step import REGISTRY, Refusal
from deckhand.steps.captain import Captain
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import setup
from tests.test_queue import asked
from tests.test_step_hand import ME, TEAM, Shell, building, clone, row, state

__all__ = ["clone", "state"]

DAY = date(2026, 11, 17)


def captain(store: FakeStore, shell: Shell | None = None, today: date = DAY) -> Captain:
    return Captain(store, shell or Shell(), person=store, today=lambda: today)


def output(store: FakeStore, args: list[str], capsys: pytest.CaptureFixture[str], **kwargs) -> str:
    captain(store, **kwargs).context([*args, *TEAM])
    return capsys.readouterr().out


def owned() -> FakeStore:
    store = building()
    store.assign("s1", ME)
    store.assign("s2", ME)
    return store


def test_registered_without_an_apply():
    assert "captain" in REGISTRY
    with pytest.raises(Refusal):
        REGISTRY["captain"].apply([])


def test_count_line_only(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    text = output(store, ["--count"], capsys)
    assert text == "1 waiting, top: question ACME-1\n"


def test_top_item_in_full(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    lines = output(store, [], capsys).splitlines()
    assert lines[0] == "1 waiting, top: question ACME-1"
    assert lines[1:4] == [
        "ACME-1 Orders",
        "Which queue?",
        "options: Redis, SQS",
    ]
    assert lines[4].startswith("next: decide context question ")
    assert "slots 0 of 1" in lines


def test_restart_prints_same_queue(clone: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    first = output(store, [], capsys)
    assert not state.exists()
    second = output(store, [], capsys)
    assert first == second


def test_idle_or_done_hand_never_watched(clone: Path, capsys: pytest.CaptureFixture[str]):
    shell = Shell(row("ACME-1", "working", "busy"), row("ACME-2", "working", "idle"), row("ACME-3", "done", "idle"))
    text = output(owned(), [], capsys, shell=shell)
    assert "watch: hand-ACME-1" in text.splitlines()
    assert "ACME-2" not in text.split("watch:")[1]
    assert "slots 2 of 1" in text.splitlines()


def test_wake_due_clears_after_wake(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.put_record("s1", Record(asks.HAND_START, "s1", "v", HAND, {"kind": "story", "session": "hand-ACME-1"}))
    answer = {"answer": "Redis", "nonce": "n1", "by": ME}
    store.put_record("s1", Record(asks.ANSWER, "q-n1", "v1", "person:session-1", answer))
    assert "wake due: ACME-1" in output(store, [], capsys).splitlines()
    store.put_record("s1", Record(asks.WAKE, "s1", "n1", HAND, {"decision": asks.ANSWER}))
    assert "wake due" not in output(store, [], capsys)


def test_cycle_printed_as_a_line(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.relate("s1", "s2")
    store.relate("s2", "s1")
    text = output(store, [], capsys)
    assert "cycle: ACME-1, ACME-2" in text.splitlines()


def test_ready_to_release(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    assert "ready to release" not in output(store, [], capsys)
    store.set_issue_state("s1", "Done")
    store.set_issue_state("s2", "Done")
    assert "ready to release: Alpha" in output(store, [], capsys).splitlines()


def test_budget_line(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.relate("s1", "s2")
    text = output(store, [], capsys).splitlines()
    assert "Alpha: day 1 of 7" in text
    assert "ACME-1 Backlog viewer-1 ready" in text
    assert "ACME-2 Backlog viewer-1 blocked by ACME-1" in text
    facts = json.loads(output(store, ["--json"], capsys))
    assert facts["budget"] == {"feature": "Alpha", "days_used": 1, "budget_days": 7, "planning_note": False}
    assert facts["notes"] == []
    planning = setup()
    note = output(planning, [], capsys, today=date(2026, 11, 20))
    assert "Alpha: day 4 of 7, planning has passed half of the week" in note.splitlines()
