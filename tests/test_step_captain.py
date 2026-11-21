from __future__ import annotations

import io
import json
import shlex
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from deckhand import answers, asks, checks, config, status
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
    assert shown[2] == "◐ 0 working · ○ 0 queued · slots 1/1 · Alpha day 1 of 7"
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
    output(store, [], capsys)
    notified = written()["items"][0]["key"]
    status.write(answers.state_dir(), written() | {"notified": [notified]})
    Captain(store, Shell(), person=NoKey(), today=lambda: DAY, clock=lambda: NOW).context(TEAM)
    shown = paragraphs(capsys.readouterr().out)
    assert shown[:3] == [
        "cannot tell what needs you: the personal Linear key could not be read; run /deckhand:setup",
        "◐ 0 working · ○ 2 queued · slots 0/1 · Alpha day 1 of 7",
        "no personal key",
    ]
    assert (written()["person_unknown"], written()["notified"]) == (True, [notified])
    assert line_of([], capsys) == "● personal key unreadable · run /deckhand:setup\n"
    out, _ = board_of([], capsys)
    assert out.splitlines()[0].startswith(" ● personal key unreadable · run /deckhand:setup · updated")
    assert "   cannot tell what needs you: the personal Linear key could not be read; run /deckhand:setup" in out
    assert "nothing needs you" not in out


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


def test_a_merged_story_carries_its_completion_time(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.set_issue_state("s1", "Done")
    output(store, [], capsys)
    done, open_ = written()["stories"]
    assert (done["ref"], open_["ref"]) == ("ACME-1", "ACME-2")
    assert datetime.fromisoformat(done["completed_at"])
    assert open_["completed_at"] is None


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


class Untouched:
    def __getattr__(self, name: str):
        raise AssertionError(f"the line touched the store: {name}")


def seeded(state: Path, **extra) -> None:
    found = {
        "shape": 1,
        "written_at": iso(int(NOW * 1000)),
        "team": "ACME",
        "feature": "Alpha",
        "budget": None,
        "slots": {"used": 0, "cap": 2},
        "items": [
            {"key": "k1", "kind": "sign-off", "ref": "ACME-1", "title": "t", "since": iso(int((NOW - 840) * 1000))}
        ],
        "stories": [],
        "hands": [],
        "release": False,
        "stale_since": None,
        "notified": [],
    }
    status.write(state, found | extra)


def line_of(args: list[str], capsys: pytest.CaptureFixture[str], shell: Shell | None = None) -> str:
    shell = shell or Shell()
    Captain(Untouched(), shell, person=Untouched(), clock=lambda: NOW).context(["--line", *args])
    assert shell.calls == []
    return capsys.readouterr().out


def test_line_reads_only_the_status_file(state: Path, capsys: pytest.CaptureFixture[str]):
    seeded(state)
    assert line_of([], capsys) == "● 1 needs you · sign-off ACME-1 14m\n"


def test_line_needs_no_team_key_or_credentials(state: Path, capsys: pytest.CaptureFixture[str]):
    seeded(state)
    Captain(clock=lambda: NOW).context(["--line"])
    assert capsys.readouterr().out.startswith("●")


def test_line_prints_nothing_without_a_file(state: Path, capsys: pytest.CaptureFixture[str]):
    assert line_of([], capsys) == ""


def test_line_prints_nothing_when_no_feature_is_open(state: Path, capsys: pytest.CaptureFixture[str]):
    seeded(state, feature=None)
    assert line_of([], capsys) == ""


def test_line_prints_nothing_for_a_damaged_file(state: Path, capsys: pytest.CaptureFixture[str]):
    seeded(state, written_at="yesterday")
    assert line_of([], capsys) == ""


def test_line_prints_nothing_when_the_state_dir_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    def fails() -> Path:
        raise Refusal("no state dir")

    monkeypatch.setattr(answers, "state_dir", fails)
    assert line_of([], capsys) == ""


def test_line_colors_only_when_asked(state: Path, capsys: pytest.CaptureFixture[str]):
    seeded(state)
    assert "\x1b" not in line_of([], capsys)
    assert line_of(["--color"], capsys).startswith("\x1b[31m")


@pytest.mark.parametrize("env", [{"NO_COLOR": "1"}, {"TERM": "dumb"}])
def test_line_honors_no_color_and_a_dumb_terminal(
    env: dict[str, str], state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    seeded(state)
    assert "\x1b" not in line_of(["--color"], capsys)


def test_line_refuses_arguments_it_cannot_combine(capsys: pytest.CaptureFixture[str]):
    for args in (["--line", "--json"], ["--line", "--brief"], ["--color"], ["--line", "--bogus"]):
        with pytest.raises(Refusal):
            Captain(Untouched(), clock=lambda: NOW).context(args)


def test_line_prints_nothing_on_stdout_through_the_cli_when_it_breaks(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from deckhand import cli

    def explode(_: Path) -> dict:
        raise RuntimeError("secret")

    monkeypatch.setattr(status, "read", explode)
    assert cli.main(["captain", "context", "--line"]) == 0
    assert capsys.readouterr().out == ""


def board_of(args: list[str], capsys: pytest.CaptureFixture[str], shell: Shell | None = None) -> tuple[str, str]:
    Captain(Untouched(), shell or Shell(), person=Untouched(), clock=lambda: NOW).context(["--board", *args])
    found = capsys.readouterr()
    return found.out, found.err


def test_board_prints_once_in_plain_text_off_a_terminal(state: Path, capsys: pytest.CaptureFixture[str]):
    seeded(state, stories=[{"ref": "ACME-2", "title": "Receipts", "state": "Backlog", "blocked_by": []}])
    shell = Shell(row("ACME-2", "working", "busy"))
    out, err = board_of([], capsys, shell)
    assert "\x1b" not in out and err == ""
    lines = out.splitlines()
    assert lines[0] == " ● 1 needs you · sign-off ACME-1 14m · updated 0s ago · Alpha · slots 1/2 · no captain running"
    assert any(line.split()[:3] == ["●", "ACME-1", "t"] for line in lines)
    assert any(line.split()[:3] == ["◐", "ACME-2", "Receipts"] for line in lines)
    assert [argv[:2] for argv, _ in shell.calls] == [["claude", "agents"]]


def test_board_without_a_file_teaches_how_to_start(state: Path, capsys: pytest.CaptureFixture[str]):
    out, _ = board_of([], capsys)
    assert "no captain has run yet; start one with /deckhand:captain" in out


def tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


def test_board_is_styled_on_a_terminal(state: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    seeded(state)
    tty(monkeypatch)
    assert "\x1b[1;31m●\x1b[0m" in board_of([], capsys)[0]
    assert "\x1b" not in board_of(["--no-color"], capsys)[0]
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\x1b" not in board_of([], capsys)[0]


def test_watch_off_a_terminal_prints_once_and_says_why(state: Path, capsys: pytest.CaptureFixture[str]):
    seeded(state)
    out, err = board_of(["--watch"], capsys)
    assert out.startswith(" ● 1 needs you · sign-off ACME-1 14m")
    assert err == "deckhand: --watch needs a terminal; printed the board once\n"


def test_watch_on_a_terminal_runs_full_screen(state: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    from deckhand import tui

    seeded(state)
    tty(monkeypatch)
    ran = {}
    monkeypatch.setattr(
        tui, "watch", lambda read, sessions, clock, color, g, refresh: ran.update(status=read(), color=color, r=refresh)
    )
    board_of(["--watch", "--no-color"], capsys)
    assert ran["status"]["feature"] == "Alpha"
    assert ran["color"] is False and callable(ran["r"])


def test_board_refuses_arguments_it_cannot_combine():
    for args in (["--watch"], ["--no-color"], ["--board", "--json"], ["--board", "--line"], ["--board", "--brief"]):
        with pytest.raises(Refusal):
            Captain(Untouched(), clock=lambda: NOW).context(args)


def test_watch_on_a_dumb_terminal_prints_once(state: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    from deckhand import tui

    seeded(state)
    tty(monkeypatch)
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr(tui, "watch", lambda *args: pytest.fail("ran full screen on a dumb terminal"))
    out, err = board_of(["--watch"], capsys)
    assert "\x1b" not in out and out.startswith(" ● 1 needs you · sign-off ACME-1 14m")
    assert err.startswith("deckhand: --watch needs a terminal")


def test_the_full_view_names_the_installed_board_launcher(
    clone: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    launcher = config.config_dir() / "deckhand"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o700)
    shown = output(owned(), [], capsys).splitlines()
    places = [f"DECKHAND_CONFIG_DIR={config.config_dir()}", f"DECKHAND_STATE_DIR={answers.state_dir()}"]
    expected = shlex.join(["env", *places, str(launcher), "captain", "context", "--board", "--watch"])
    assert shown[-1] == f"board: {expected}"
    assert shown.count(shown[-1]) == 1


def test_the_board_line_carries_no_folders_at_their_defaults(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from deckhand.steps import captain

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DECKHAND_CONFIG_DIR", raising=False)
    monkeypatch.delenv("DECKHAND_STATE_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["/opt/deckhand/bin/deckhand"])
    assert captain._board_command() == "/opt/deckhand/bin/deckhand captain context --board --watch"


def test_without_a_launcher_the_board_line_names_this_deckhand(
    clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    program = tmp_path / "plug in" / "bin" / "deckhand"
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "argv", [str(program.parent / ".." / "bin" / "deckhand"), "captain", "context"])
    shown = output(owned(), [], capsys).splitlines()
    assert shown[-1].endswith(f" {shlex.quote(str(program.resolve()))} captain context --board --watch")


def test_the_brief_and_the_json_carry_no_board_line(clone: Path, capsys: pytest.CaptureFixture[str]):
    assert "board:" not in output(owned(), ["--brief"], capsys)
    assert "board:" not in output(owned(), ["--json"], capsys)


def test_a_teammates_started_story_is_never_a_stopped_hand(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.assign("s2", "someone-else")
    store.put_record("s2", Record(checks.KIND, "s2", "v1", HAND, {}))
    text = output(store, ["--brief"], capsys)
    assert text.startswith("◐ 0 working · ○ 1 queued · nothing needs you")
    assert [(s["ref"], s["mine"]) for s in written()["stories"]] == [("ACME-1", True), ("ACME-2", False)]


BAD_FILES = [
    {"shape": 1, "notified": []},
    {"written_at": "garbage"},
    {"items": [{"key": "k1", "kind": "sign-off", "ref": "ACME-1", "title": "t", "since": "bad"}]},
    {"slots": {}},
    {"budget": {"feature": "Alpha"}},
    {"stories": [{"ref": "ACME-1", "state": "Backlog", "stage": {"round": 1}}]},
]


@pytest.mark.parametrize("mode", [[], ["--brief"], ["--json"]])
@pytest.mark.parametrize("bad", BAD_FILES)
def test_a_malformed_earlier_file_reads_as_no_earlier_state(
    clone: Path, bad: dict, mode: list[str], capsys: pytest.CaptureFixture[str]
):
    if bad.get("shape"):
        status.write(answers.state_dir(), bad)
    else:
        seeded(answers.state_dir(), **bad)
    text = output(unreachable(owned(), "relations"), mode, capsys, clock=NOW + 60)
    shown = json.loads(text)["brief"] if mode == ["--json"] else text
    assert shown.splitlines()[0] == "Linear unreachable; no earlier state"


def test_a_captain_write_with_no_feature_open_reads_back(clone: Path, capsys: pytest.CaptureFixture[str]):
    output(FakeStore(), [], capsys)
    assert written() is not None and written()["feature"] is None


def test_the_board_refresh_survives_a_missing_program_and_a_hung_read(monkeypatch: pytest.MonkeyPatch):
    from deckhand.steps import captain

    monkeypatch.setattr(captain.sys, "argv", ["/nowhere/deckhand"])
    assert captain._full_read()() == captain.NOT_RUN

    class Hung:
        killed = False

        def poll(self):
            return None

        def kill(self):
            Hung.killed = True

        def wait(self):
            return -9

    now = [0.0]
    monkeypatch.setattr(captain.subprocess, "Popen", lambda *a, **k: Hung())
    running = captain._full_read(lambda: now[0])
    assert running() is None
    now[0] = captain.REFRESH_LIMIT + 1
    assert running() == -9 and Hung.killed
