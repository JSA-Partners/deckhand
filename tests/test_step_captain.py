from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from deckhand import answers, asks, config, status
from deckhand.features import FEATURE_LABEL
from deckhand.linear.client import LinearError
from deckhand.records import HAND, Record
from deckhand.step import REGISTRY, Refusal
from deckhand.steps.captain import Captain, _policy_for
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import setup
from tests.test_queue import asked
from tests.test_step_hand import ME, TEAM, Shell, building, clone, row, spike_row, state

__all__ = ["clone", "state"]

DAY = date(2026, 11, 17)
NOW = 1795000000.0


def captain(store: FakeStore, shell: Shell | None = None, today: date = DAY) -> Captain:
    return Captain(store, shell or Shell(), person=store, today=lambda: today, clock=lambda: NOW)


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


def test_restart_prints_same_queue(clone: Path, state: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    shell = Shell(row("ACME-1", "working", "busy"))
    first = output(store, [], capsys, shell=shell)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(store.snapshot()))
    restarted = FakeStore.from_snapshot(snapshot)
    assert [p.name for p in state.iterdir()] == [status.FILE]
    assert output(restarted, [], capsys, shell=Shell(row("ACME-1", "working", "busy"))) == first
    assert "question ACME-1" in first


def test_bad_config_degrades_to_a_note(clone: Path, capsys: pytest.CaptureFixture[str]):
    (config.config_dir() / config.FILE).write_text("nonsense = 1\n")
    text = output(owned(), [], capsys)
    assert "unknown key nonsense" in text.splitlines()[0]
    assert "nothing waiting" in text
    assert "ACME-1 Backlog viewer-1 ready" in text.splitlines()


def test_two_open_features_degrade_to_a_note(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.create_project("p2", "ACME", "Beta", "", [FEATURE_LABEL])
    store.set_project_dates("p2", "2026-11-16", "2026-11-23")
    text = output(store, [], capsys)
    assert "more than one feature is open" in text.splitlines()[0]
    assert "slots 0 of 1" in text.splitlines()


def test_missing_personal_key_degrades_to_a_note(clone: Path, capsys: pytest.CaptureFixture[str]):
    class NoKey:
        def viewer(self):
            raise Refusal("no personal key")

    store = owned()
    asked(store, "s1")
    Captain(store, Shell(), person=NoKey(), today=lambda: DAY).context(TEAM)
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == ["no personal key", "nothing waiting"]


def test_missing_clone_degrades_to_a_note():
    notes: list[str] = []
    policy = _policy_for(notes)({"labels": ["widgets"]})
    assert policy.second_signoff is False
    assert [n.startswith("no clone for widgets in ") for n in notes] == [True]


def test_failed_fleet_read_prints_its_note(clone: Path, capsys: pytest.CaptureFixture[str]):
    text = output(owned(), [], capsys, shell=Shell(fleet_fails=True))
    assert text.splitlines()[0].startswith("the fleet read failed")


def test_idle_or_done_hand_never_watched(clone: Path, capsys: pytest.CaptureFixture[str]):
    shell = Shell(row("ACME-1", "working", "busy"), row("ACME-2", "working", "idle"), row("ACME-3", "done", "idle"))
    text = output(owned(), [], capsys, shell=shell)
    assert "watch: hand-ACME-1" in text.splitlines()
    assert "ACME-2" not in text.split("watch:")[1]
    assert "slots 2 of 1" in text.splitlines()


def test_a_busy_spike_is_watched(clone: Path, capsys: pytest.CaptureFixture[str]):
    shell = Shell(row("ACME-1", "working", "busy"), spike_row("0123456789ab", "working", "busy"))
    text = output(owned(), [], capsys, shell=shell)
    assert "watch: hand-ACME-1, spike-0123456789ab" in text.splitlines()


def test_wake_due_clears_after_wake(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.put_record("s1", Record(asks.HAND_START, "s1", "v", HAND, {"kind": "story", "session": "hand-ACME-1"}))
    asked = {"question": "Which queue?", "options": ["Redis", "SQS"], "why": "Both fit."}
    store.put_record("s1", Record(asks.QUESTION, "s1:q-n1", "v1", HAND, asked))
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


def written() -> dict | None:
    return status.read(answers.state_dir())


def test_a_full_read_writes_the_status_file(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    store.relate("s1", "s2")
    output(store, [], capsys, shell=Shell(row("ACME-1", "working", "busy")))
    found = written()
    assert found["written_at"] == "2026-11-18T11:06:40.000Z"
    assert (found["team"], found["feature"], found["slots"]) == ("ACME", "Alpha", {"used": 1, "cap": 1})
    assert [(i["kind"], i["ref"]) for i in found["items"]] == [("question", "ACME-1")]
    assert [(s["ref"], s["title"], s["blocked_by"], s["stage"]["name"]) for s in found["stories"]] == [
        ("ACME-1", "Orders", [], "planning"),
        ("ACME-2", "Receipts", ["ACME-1"], "planning"),
    ]
    assert [(h["name"], h["ref"]) for h in found["hands"]] == [("hand-ACME-1", "ACME-1")]


def test_the_json_read_writes_the_file_and_shows_the_stage(clone: Path, capsys: pytest.CaptureFixture[str]):
    facts = json.loads(output(owned(), ["--json"], capsys))
    assert facts["order"][0]["stage"]["name"] == "planning"
    assert written()["stories"] == facts["order"]


def test_the_count_writes_nothing(clone: Path, capsys: pytest.CaptureFixture[str]):
    output(owned(), ["--count"], capsys)
    assert written() is None


def test_a_later_read_keeps_what_was_notified(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    output(store, [], capsys)
    first = written()
    status.write(answers.state_dir(), first | {"notified": [first["items"][0]["key"]]})
    output(store, [], capsys)
    assert written()["notified"] == [first["items"][0]["key"]]


def test_linear_unreachable_keeps_the_last_file(clone: Path, capsys: pytest.CaptureFixture[str]):
    class Unreachable:
        def viewer(self):
            raise LinearError("network", "could not reach Linear")

    store = owned()
    asked(store, "s1")
    output(store, [], capsys)
    before = written()
    Captain(store, Shell(), person=Unreachable(), today=lambda: DAY, clock=lambda: NOW + 60).context(TEAM)
    assert "could not reach Linear" in capsys.readouterr().out
    assert written() == before


def test_a_failed_write_is_a_note(clone: Path, capsys: pytest.CaptureFixture[str]):
    answers.state_dir().parent.mkdir(parents=True, exist_ok=True)
    answers.state_dir().write_text("not a directory")
    text = output(owned(), [], capsys)
    assert text.splitlines()[0].startswith(f"cannot write {answers.state_dir() / status.FILE}")
    assert "slots 0 of 1" in text.splitlines()


@pytest.mark.parametrize("notified", [None, [["a"]], [1], "abc"])
def test_a_bad_notified_is_rewritten(clone: Path, notified: object, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    output(store, [], capsys)
    path = answers.state_dir() / status.FILE
    path.write_text(json.dumps(json.loads(path.read_text()) | {"notified": notified}))
    assert "1 waiting, top: question ACME-1" in output(store, [], capsys).splitlines()
    assert written()["notified"] == []
