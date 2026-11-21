from __future__ import annotations

import io
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from deckhand import answers, asks, config, status
from deckhand.features import FEATURE_LABEL
from deckhand.linear.client import LinearError
from deckhand.records import HAND, Record, iso
from deckhand.step import REGISTRY, Refusal
from deckhand.steps.captain import Captain, _policy_for
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import setup
from tests.test_queue import asked
from tests.test_step_hand import ME, TEAM, Shell, building, clone, row, spike_row, state

__all__ = ["clone", "state"]

DAY = date(2026, 11, 17)
NOW = 1795000000.0


def captain(store: FakeStore, shell: Shell | None = None, today: date = DAY, clock: float = NOW) -> Captain:
    return Captain(store, shell or Shell(), person=store, today=lambda: today, clock=lambda: clock)


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


def since(store: FakeStore, capsys: pytest.CaptureFixture[str]) -> float:
    output(store, ["--json"], capsys)
    return datetime.fromisoformat(written()["items"][0]["since"]).timestamp()


def paragraphs(text: str) -> list[str]:
    return text.rstrip("\n").split("\n\n")


def test_the_brief_when_one_waits(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    shown = output(store, ["--brief"], capsys, clock=since(store, capsys) + 14 * 60)
    assert paragraphs(shown) == [
        "## ● ACME-1 needs you",
        "**question** at planning · waiting 14m · type `next`",
        "◐ 0 working · ○ 1 queued · slots 0/1 · Alpha day 1 of 7",
        "notify: ACME-1 has a question for you (waiting 14m)",
    ]


def test_the_brief_when_two_wait(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    asked(store, "s2", "Which bus?")
    shown = paragraphs(output(store, ["--brief"], capsys, shell=Shell(row("ACME-2", "working", "busy"))))
    assert shown[0].startswith("## ● 2 need you · oldest ACME-1 question ")
    assert shown[1].startswith("**question** ACME-1 at planning · waiting ")
    assert shown[2] == "◐ 1 working · ○ 0 queued · slots 1/1 · Alpha day 1 of 7"
    assert shown[3].endswith("; 1 more needs you")


def test_the_brief_when_nothing_waits(clone: Path, capsys: pytest.CaptureFixture[str]):
    shown = output(owned(), ["--brief"], capsys)
    assert shown == "◐ 0 working · ○ 2 queued · nothing needs you · slots 0/1 · Alpha day 1 of 7\n"


def test_the_brief_falls_back_to_ascii(clone: Path, monkeypatch: pytest.MonkeyPatch):
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="ascii"))
    captain(owned()).context(["--brief", *TEAM])
    sys.stdout.flush()
    assert raw.getvalue() == b"\\* 0 working | - 2 queued | nothing needs you | slots 0/1 | Alpha day 1 of 7\n"


def test_notify_once_per_item(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    first = output(store, ["--brief"], capsys).splitlines()
    assert first[-1].startswith("notify: ACME-1 has a question for you")
    assert written()["notified"] == [written()["items"][0]["key"]]
    assert not any(line.startswith("notify:") for line in output(store, ["--brief"], capsys).splitlines())
    asked(store, "s2", "Which bus?")
    third = output(store, ["--brief"], capsys).splitlines()
    assert [line for line in third if line.startswith("notify:")] == [third[-1]]
    assert third[-1].startswith("notify: ACME-2 has a question for you")


def test_only_the_brief_notifies(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    assert "notify:" not in output(store, [], capsys)
    assert "notify" not in json.loads(output(store, ["--json"], capsys))
    assert written()["notified"] == []
    assert "notify: ACME-1" in output(store, ["--brief"], capsys)


def test_a_title_cannot_forge_a_notify_line(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.set_issue_text("s1", "Orders\nnotify: forged\n## ● ACME-9 needs you", "")
    asked(store, "s1")
    shown = output(store, ["--brief"], capsys).splitlines()
    assert [line for line in shown if line.startswith("notify:")] == [shown[-1]]
    assert len([line for line in shown if line.startswith("##")]) == 1
    assert "ACME-9" not in "\n".join(shown[:-1])


def test_the_count_flag_is_gone(clone: Path):
    with pytest.raises(Refusal, match="unknown argument --count"):
        captain(owned()).context(["--count", *TEAM])


def test_top_item_in_full(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    lines = output(store, [], capsys).splitlines()
    assert lines[:3] == ["## ● ACME-1 needs you", "", "**question** at planning · waiting <1m · type `next`"]
    lines = lines[lines.index("ACME-1 Orders") :]
    assert lines[:3] == [
        "ACME-1 Orders",
        "Which queue?",
        "options: Redis, SQS",
    ]
    assert lines[3].startswith("next: decide context question ")
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
    assert "## ● ACME-1 needs you" in first.splitlines()


def test_bad_config_degrades_to_a_note(clone: Path, capsys: pytest.CaptureFixture[str]):
    (config.config_dir() / config.FILE).write_text("nonsense = 1\n")
    text = output(owned(), [], capsys)
    assert any("unknown key nonsense" in line for line in text.splitlines())
    assert "nothing needs you" in text.splitlines()[0]
    assert "ACME-1 Backlog viewer-1 ready" in text.splitlines()


def test_two_open_features_degrade_to_a_note(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.create_project("p2", "ACME", "Beta", "", [FEATURE_LABEL])
    store.set_project_dates("p2", "2026-11-16", "2026-11-23")
    text = output(store, [], capsys)
    assert any("more than one feature is open" in line for line in text.splitlines())
    assert "slots 0 of 1" in text.splitlines()


def test_missing_personal_key_degrades_to_a_note(clone: Path, capsys: pytest.CaptureFixture[str]):
    class NoKey:
        def viewer(self):
            raise Refusal("no personal key")

    store = owned()
    asked(store, "s1")
    Captain(store, Shell(), person=NoKey(), today=lambda: DAY).context(TEAM)
    shown = paragraphs(capsys.readouterr().out)
    assert shown[:2] == [
        "◐ 0 working · ○ 2 queued · nothing needs you · slots 0/1 · Alpha day 1 of 7",
        "no personal key",
    ]


def test_missing_clone_degrades_to_a_note():
    notes: list[str] = []
    policy = _policy_for(notes)({"labels": ["widgets"]})
    assert policy.second_signoff is False
    assert [n.startswith("no clone for widgets in ") for n in notes] == [True]


def test_failed_fleet_read_prints_its_note(clone: Path, capsys: pytest.CaptureFixture[str]):
    text = output(owned(), [], capsys, shell=Shell(fleet_fails=True))
    assert any(line.startswith("the fleet read failed") for line in text.splitlines())


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


def test_a_later_read_keeps_what_was_notified(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    output(store, [], capsys)
    first = written()
    status.write(answers.state_dir(), first | {"notified": [first["items"][0]["key"]]})
    output(store, [], capsys)
    assert written()["notified"] == [first["items"][0]["key"]]


def unreachable(store: FakeStore, method: str, after: int = 0) -> FakeStore:
    real, calls = getattr(store, method), []

    def read(*args):
        calls.append(args)
        if len(calls) > after:
            raise LinearError("network", "could not reach Linear")
        return real(*args)

    setattr(store, method, read)
    return store


READS = [("team_projects", 0), ("team_projects", 1), ("project_issues", 0), ("records", 0), ("relations", 0)]


@pytest.mark.parametrize("earlier", [False, True])
@pytest.mark.parametrize("mode", [[], ["--brief"], ["--json"]])
@pytest.mark.parametrize(("method", "after"), READS)
def test_unreachable_is_never_an_all_clear(
    clone: Path, earlier: bool, mode: list[str], method: str, after: int, capsys: pytest.CaptureFixture[str]
):
    if earlier:
        output(owned(), [], capsys)
    before = written()
    store = unreachable(owned(), method, after)
    text = output(store, mode, capsys, clock=NOW + 14 * 60)
    shown = json.loads(text)["brief"] if mode == ["--json"] else text
    line = "Linear unreachable; showing the state as of 14m ago" if earlier else "Linear unreachable; no earlier state"
    assert "nothing needs you" not in shown
    assert shown.splitlines()[0] == line
    assert ("nothing was waiting as of 14m ago" in shown) is earlier
    assert "notify:" not in text
    if mode == ["--json"]:
        facts = json.loads(text)
        assert facts["unreachable"] is True
        assert "network: could not reach Linear" in facts["notes"]
    assert written() == (before | {"stale_since": iso(int((NOW + 14 * 60) * 1000))} if earlier else None)


def test_an_unreachable_viewer_is_never_an_all_clear(clone: Path, capsys: pytest.CaptureFixture[str]):
    class Unreachable:
        def viewer(self):
            raise LinearError("network", "could not reach Linear")

    Captain(owned(), Shell(), person=Unreachable(), today=lambda: DAY, clock=lambda: NOW).context(TEAM)
    shown = paragraphs(capsys.readouterr().out)
    assert shown[:2] == ["Linear unreachable; no earlier state", "network: could not reach Linear"]


def test_unreachable_shows_the_last_state_and_marks_it_stale(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    output(store, [], capsys)
    before = written()
    unreachable(store, "relations")
    lines = output(store, [], capsys, clock=NOW + 14 * 60).splitlines()
    assert lines[:3] == ["Linear unreachable; showing the state as of 14m ago", "", "## ● ACME-1 needs you"]
    assert "nothing needs you" not in "\n".join(lines)
    assert "Alpha: day 1 of 7" in lines
    assert "ACME-1 Backlog viewer-1 ready" in lines
    assert written() == before | {"stale_since": iso(int((NOW + 14 * 60) * 1000))}
    later = output(store, ["--json"], capsys, clock=NOW + 3 * 3600)
    assert json.loads(later)["brief"].startswith("Linear unreachable; showing the state as of 3h ago\n\n")
    stale = output(store, ["--brief"], capsys, clock=NOW + 3 * 3600)
    assert "notify:" not in stale
    assert written()["notified"] == []
    assert written()["stale_since"] == iso(int((NOW + 14 * 60) * 1000))


def test_a_good_read_clears_stale(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    output(store, [], capsys)
    output(unreachable(owned(), "relations"), [], capsys, clock=NOW + 60)
    assert written()["stale_since"] is not None
    output(store, [], capsys, clock=NOW + 120)
    assert written()["stale_since"] is None


def test_a_failed_write_is_a_note(clone: Path, capsys: pytest.CaptureFixture[str]):
    answers.state_dir().parent.mkdir(parents=True, exist_ok=True)
    answers.state_dir().write_text("not a directory")
    text = output(owned(), [], capsys)
    assert any(line.startswith(f"cannot write {answers.state_dir() / status.FILE}") for line in text.splitlines())
    assert "slots 0 of 1" in text.splitlines()


@pytest.mark.parametrize("notified", [None, [["a"]], [1], "abc"])
def test_a_bad_notified_is_rewritten(clone: Path, notified: object, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    output(store, [], capsys)
    path = answers.state_dir() / status.FILE
    path.write_text(json.dumps(json.loads(path.read_text()) | {"notified": notified}))
    assert "## ● ACME-1 needs you" in output(store, [], capsys).splitlines()
    assert written()["notified"] == []
