from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

from deckhand import answers, checks, config, fleet, launch, reading, status, watch
from deckhand.git import GitError
from deckhand.records import HAND, Record, iso
from deckhand.step import REGISTRY, Refusal
from deckhand.steps import captain
from deckhand.steps.after import After
from deckhand.steps.captain import Captain
from deckhand.steps.decide import Decide
from deckhand.store import local
from deckhand.store import open as store_open
from deckhand.store.fake import FakeStore
from deckhand.store.local import default_path
from deckhand.store.state import STALE_AFTER, LocalState
from tests.shell import Shell, row
from tests.test_chores import opened_next, released
from tests.test_feature_approval import P1, TEAM, setup
from tests.test_queue import asked
from tests.test_status import write
from tests.test_step_hand import ME, building, clone, state
from tests.test_step_review import answer

__all__ = ["clone", "state"]

DAY = date(2026, 11, 17)
NOW = 1795000000.0


def beat(at: float = NOW) -> None:
    with LocalState(default_path()) as rows:
        rows.beat(4242, at)


def engine_wrote(
    store: FakeStore, shell: Shell | None = None, person: object = None, today: date = DAY, clock: float = NOW
) -> None:
    read = fleet.sessions(shell or Shell())
    found = reading.read(store, lambda: person or store, "ACME", read, config.local(), today)
    shown = status.snapshot(iso(int(clock * 1000)), "ACME", found.facts, found.waiting, read.sessions, found.me is None)
    write(answers.state_dir(), shown)
    beat(clock)


def output(args: list[str], capsys: pytest.CaptureFixture[str], clock: float = NOW) -> str:
    capsys.readouterr()
    Captain(Shell(), clock=lambda: clock).context(args)
    return capsys.readouterr().out


def owned() -> FakeStore:
    store = building()
    store.assign("s1", ME)
    store.assign("s2", ME)
    return store


def paragraphs(text: str) -> list[str]:
    return text.rstrip("\n").split("\n\n")


@pytest.mark.parametrize("args", [[], ["park"], ["hold", "release"], ["--json"]])
def test_captain_apply_takes_hold_or_release(state: Path, args: list[str]):
    with pytest.raises(Refusal):
        REGISTRY["captain"].apply(args)
    with LocalState(state / "deckhand.db") as rows:
        assert rows.get(watch.HOLD) is None


def test_hold_and_release_set_and_clear_the_row(state: Path, capsys: pytest.CaptureFixture[str]):
    REGISTRY["captain"].apply(["hold"])
    assert capsys.readouterr().out == "done\n"
    with LocalState(state / "deckhand.db") as rows:
        assert rows.get(watch.HOLD) is not None
    REGISTRY["captain"].apply(["release"])
    assert capsys.readouterr().out == "done\n"
    with LocalState(state / "deckhand.db") as rows:
        assert rows.get(watch.HOLD) is None


def test_captain_context_takes_no_words(state: Path):
    with pytest.raises(Refusal, match="unknown argument hold"):
        Captain(Shell()).context(["hold"])


@pytest.mark.parametrize("flag", ["--brief", "--watch", "--count", "--team-key"])
def test_the_flags_of_a_reading_captain_are_gone(state: Path, flag: str):
    with pytest.raises(Refusal, match=f"unknown argument {flag}"):
        Captain(Shell()).context([flag])


class Raising:
    def __call__(self, *args: object) -> None:
        raise GitError(["claude", "agents"], 1, "the fleet was read")


def test_captain_context_reads_no_linear_and_no_fleet(
    clone: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    store = owned()
    asked(store, "s1")
    engine_wrote(store)

    def opened(role: str, teams: object = None) -> None:
        raise AssertionError(f"the captain opened the {role} store")

    monkeypatch.setattr(store_open, "open_store", opened)
    capsys.readouterr()
    Captain(Raising(), clock=lambda: NOW).context([])
    shown = capsys.readouterr().out.splitlines()
    top = shown[shown.index("engine: running") + 1 :]
    assert top[:3] == ["ACME-1 Orders", "Which queue?", "options: Redis, SQS"]
    assert top[3].startswith("next: decide context question ")
    assert shown[0] == "## ● ACME-1 needs you"


def test_captain_context_with_a_stale_heartbeat_withholds_every_item(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    engine_wrote(store)
    assert output([], capsys, clock=NOW + STALE_AFTER + 1) == "engine: not running\n"
    assert json.loads(output(["--json"], capsys, clock=NOW + STALE_AFTER + 1)) == {
        "engine": "engine: not running",
        "brief": None,
        "top": None,
        "release": None,
        "cycle": [],
        "settings": None,
    }


def test_captain_context_without_a_heartbeat_says_the_engine_is_not_running(state: Path, capsys):
    assert output([], capsys) == "engine: not running\n"


def test_a_running_engine_with_no_snapshot_yet_says_so(state: Path, capsys: pytest.CaptureFixture[str]):
    beat()
    assert output([], capsys).splitlines() == ["engine: running", "the engine has not read Linear yet"]


def test_the_brief_when_one_waits(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    engine_wrote(store)
    later = datetime.fromisoformat(status.read(answers.state_dir())["items"][0]["since"]).timestamp() + 14 * 60
    beat(later)
    assert paragraphs(output([], capsys, clock=later))[:3] == [
        "## ● ACME-1 needs you",
        "**question** at planning · waiting 14m",
        "◐ 0 working · ○ 1 queued · slots 0/1 · Alpha day 1 of 7",
    ]


def test_the_brief_when_two_wait(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    asked(store, "s2", "Which bus?")
    engine_wrote(store, Shell(row("ACME-2", "working", "busy")))
    shown = paragraphs(output([], capsys))
    assert shown[0].startswith("## ● 2 need you · oldest ACME-1 question ")
    assert shown[1].startswith("**question** ACME-1 at planning · waiting ")
    assert shown[2] == "◐ 0 working · ○ 0 queued · slots 1/1 · Alpha day 1 of 7"


def test_the_brief_when_nothing_waits(clone: Path, capsys: pytest.CaptureFixture[str]):
    engine_wrote(owned())
    assert output([], capsys).splitlines() == [
        "◐ 0 working · ○ 2 queued · nothing needs you · slots 0/1 · Alpha day 1 of 7",
        "",
        "engine: running",
    ]


def test_the_brief_falls_back_to_ascii(clone: Path, monkeypatch: pytest.MonkeyPatch):
    engine_wrote(owned())
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="ascii"))
    Captain(Shell(), clock=lambda: NOW).context([])
    sys.stdout.flush()
    assert (
        raw.getvalue().splitlines()[0]
        == b"\\* 0 working | - 2 queued | nothing needs you | slots 0/1 | Alpha day 1 of 7"
    )


def test_a_title_cannot_forge_a_heading(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.set_issue_text("s1", "Orders\n## ● ACME-9 needs you", "")
    asked(store, "s1")
    engine_wrote(store)
    shown = output([], capsys).splitlines()
    assert len([line for line in shown if line.startswith("##")]) == 1


def test_a_feature_name_cannot_forge_a_heading_in_the_release_line(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store._projects[P1]["name"] = "Alpha\n## ● ACME-9 needs you"
    store.set_issue_state("s1", "Done")
    store.set_issue_state("s2", "Done")
    engine_wrote(store)
    shown = output([], capsys).splitlines()
    assert shown[-1] == "ready to release: Alpha ## ● ACME-9 needs you"
    assert not [line for line in shown if line.startswith("##")]


def test_ready_to_release(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    engine_wrote(store)
    assert "ready to release" not in output([], capsys)
    store.set_issue_state("s1", "Done")
    store.set_issue_state("s2", "Done")
    engine_wrote(store)
    assert output([], capsys).splitlines()[-1] == "ready to release: Alpha"
    assert json.loads(output(["--json"], capsys))["release"] == "Alpha"


def test_captain_context_holds_the_release_line_while_a_chore_is_open(
    clone: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = released(["rotate the webhook secret"])
    engine_wrote(store)
    assert "ready to release" not in output([], capsys)
    After(store).apply(["ACME-1", "done", "1", "--note", "x", *TEAM])
    engine_wrote(store)
    assert output([], capsys).splitlines()[-1] == "ready to release: Alpha"
    store = released(["rotate the webhook secret"])
    After(store).apply(["ACME-1", "person", "1", "--why", "the secret lives in the vault", *TEAM])
    engine_wrote(store)
    assert "ready to release" not in output([], capsys)
    answer(state, Decide(store), capsys, ["after-merge", "ACME-1", "--item", "1"], "Done")
    engine_wrote(store)
    assert output([], capsys).splitlines()[-1] == "ready to release: Alpha"


def test_an_open_chore_in_an_older_released_feature_holds_the_release_line(
    clone: Path, capsys: pytest.CaptureFixture[str]
):
    store = released(["rotate the webhook secret"])
    gamma = opened_next(store)
    store.set_project_dates(gamma, "2026-11-23", "2026-11-30")
    store.set_issue_state("s3", "Done")
    engine_wrote(store)
    assert "ready to release" not in output([], capsys)
    After(store).apply(["ACME-1", "done", "1", "--note", "x", *TEAM])
    engine_wrote(store)
    assert output([], capsys).splitlines()[-1] == "ready to release: Beta"


def test_the_json_carries_the_brief_the_engine_and_the_top_item(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    engine_wrote(store)
    facts = json.loads(output(["--json"], capsys))
    assert (facts["engine"], facts["release"]) == ("engine: running", None)
    assert facts["brief"].startswith("## ● ACME-1 needs you")
    assert facts["top"]["lines"][0] == "ACME-1 Orders"
    assert facts["top"]["next"][0].startswith("decide context question ")


def test_an_unknown_person_is_never_an_all_clear(clone: Path, capsys: pytest.CaptureFixture[str]):
    class NoKey:
        def viewer(self):
            raise Refusal("no personal key")

    store = owned()
    asked(store, "s1")
    engine_wrote(store, person=NoKey())
    shown = paragraphs(output([], capsys))
    assert shown[:2] == [
        "cannot tell what needs you: the personal Linear key could not be read; run /deckhand:setup",
        "◐ 0 working · ○ 2 queued · slots 0/1 · Alpha day 1 of 7",
    ]
    assert line_of([], capsys) == "● personal key unreadable · run /deckhand:setup\n"
    out, _ = board_of([], capsys)
    assert out.splitlines()[0].startswith(" ● personal key unreadable · run /deckhand:setup · updated")
    assert "nothing needs you" not in out


def test_a_stale_snapshot_shows_when_linear_was_last_read(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    asked(store, "s1")
    engine_wrote(store)
    write(answers.state_dir(), status.stale(status.read(answers.state_dir()), iso(int(NOW * 1000))))
    beat(NOW + 14 * 60)
    lines = output([], capsys, clock=NOW + 14 * 60).splitlines()
    assert lines[:3] == ["Linear unreachable; showing the state as of 14m ago", "", "## ● ACME-1 needs you"]


def test_a_teammates_started_story_is_never_a_stopped_hand(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.assign("s2", "someone-else")
    store.put_record("s2", Record(checks.KIND, "s2", "v1", HAND, {}))
    engine_wrote(store)
    assert output([], capsys).startswith("◐ 0 working · ○ 1 queued · nothing needs you")


def test_planning_past_half_the_week_shows_in_the_brief(clone: Path, capsys: pytest.CaptureFixture[str]):
    engine_wrote(setup(), today=date(2026, 11, 20))
    assert "Alpha day 4 of 7" in output([], capsys)


def test_a_build_order_cycle_is_a_line_for_the_person(clone: Path, capsys: pytest.CaptureFixture[str]):
    store = owned()
    store.relate("s1", "s2")
    store.relate("s2", "s1")
    engine_wrote(store)
    assert "build order cycle: ACME-1, ACME-2; break it in Linear" in output([], capsys).splitlines()
    assert json.loads(output(["--json"], capsys))["cycle"] == ["ACME-1", "ACME-2"]


def test_board_refuses_arguments_it_cannot_combine(state: Path):
    for args in (["--watch"], ["--no-color"], ["--board", "--json"], ["--board", "--line"], ["--board", "--brief"]):
        with pytest.raises(Refusal):
            Captain(Shell(), clock=lambda: NOW).context(args)


BAD_FILES = [
    {"shape": 1},
    {"written_at": "garbage"},
    {"items": [{"key": "k1", "kind": "sign-off", "ref": "ACME-1", "title": "t", "since": "bad"}]},
    {"slots": {}},
    {"budget": {"feature": "Alpha"}},
    {"stories": [{"ref": "ACME-1", "state": "Backlog", "stage": {"round": 1}}]},
]


@pytest.mark.parametrize("bad", BAD_FILES)
def test_a_malformed_snapshot_reads_as_none_yet(state: Path, bad: dict, capsys: pytest.CaptureFixture[str]):
    if bad.get("shape"):
        write(answers.state_dir(), bad)
    else:
        seeded(answers.state_dir(), **bad)
    beat()
    assert output([], capsys).splitlines() == ["engine: running", "the engine has not read Linear yet"]


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
        "person_unknown": False,
    }
    write(state, found | extra)


def line_of(args: list[str], capsys: pytest.CaptureFixture[str], shell: Shell | None = None) -> str:
    shell = shell or Shell()
    Captain(shell, clock=lambda: NOW).context(["--line", *args])
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
    for args in (["--line", "--json"], ["--color"], ["--line", "--bogus"]):
        with pytest.raises(Refusal):
            Captain(Shell(), clock=lambda: NOW).context(args)


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
    Captain(shell or Shell(), clock=lambda: NOW).context(["--board", *args])
    found = capsys.readouterr()
    return found.out, found.err


def test_board_prints_once_in_plain_text_off_a_terminal(state: Path, capsys: pytest.CaptureFixture[str]):
    seeded(state, stories=[{"ref": "ACME-2", "title": "Receipts", "state": "Backlog", "blocked_by": []}])
    shell = Shell(row("ACME-2", "working", "busy"))
    out, err = board_of([], capsys, shell)
    assert "\x1b" not in out and err == ""
    lines = out.splitlines()
    assert lines[0] == " ● 1 needs you · sign-off ACME-1 14m · updated 0s ago · Alpha · slots 0/2 · no captain running"
    assert any(line.split()[:3] == ["●", "ACME-1", "t"] for line in lines)
    assert any(line.split()[:3] == ["◐", "ACME-2", "Receipts"] for line in lines)
    assert [argv[:2] for argv, _ in shell.calls] == [["claude", "agents"]]


def test_board_without_a_snapshot_says_linear_is_being_read(state: Path, capsys: pytest.CaptureFixture[str]):
    out, _ = board_of([], capsys)
    assert "reading Linear for the first time" in out


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


def test_context_names_a_corrupt_record_file_through_the_cli_and_exits_zero(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from deckhand import cli

    state.mkdir(parents=True)
    (state / "deckhand.db").write_bytes(b"not a database, just some bytes" * 100)
    monkeypatch.setitem(REGISTRY, "captain", Captain(clock=lambda: NOW))
    assert cli.main(["captain", "context"]) == 0
    assert capsys.readouterr().out == f"deckhand: captain context: deckhand.db in the state folder {state} is corrupt\n"


def test_context_with_a_locked_record_file_prints_one_line_and_exits_zero(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from deckhand import cli

    state.mkdir(parents=True)
    monkeypatch.setattr(local, "BUSY_SECONDS", 0)
    holder = sqlite3.connect(state / "deckhand.db", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    monkeypatch.setitem(REGISTRY, "captain", Captain(clock=lambda: NOW))
    try:
        assert cli.main(["captain", "context"]) == 0
    finally:
        holder.close()
    expected = f"deckhand: captain context: deckhand.db in the state folder {state} is locked by another process\n"
    assert capsys.readouterr().out == expected


def test_context_with_an_unreadable_record_file_prints_one_line_and_exits_zero(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from deckhand import cli

    LocalState(default_path()).close()
    (state / "deckhand.db").chmod(0)
    monkeypatch.setitem(REGISTRY, "captain", Captain(clock=lambda: NOW))
    try:
        assert cli.main(["captain", "context"]) == 0
    finally:
        (state / "deckhand.db").chmod(0o600)
    expected = f"deckhand: captain context: deckhand.db in the state folder {state} cannot be opened\n"
    assert capsys.readouterr().out == expected


def installed(settings: dict | str) -> Path:
    path = config.config_dir() / launch.CAPTAIN_SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings if isinstance(settings, str) else json.dumps(settings, indent=2) + "\n")
    return path


def written() -> dict:
    clones = config.local().clones.values()
    return launch.captain_settings(launch.protected(config.config_dir(), answers.state_dir()), clones=clones)


def test_captain_settings_this_version_writes_add_no_line(state: Path, capsys: pytest.CaptureFixture[str]):
    installed(written())
    assert output([], capsys) == "engine: not running\n"


@pytest.mark.parametrize("setup_temp", ["sandbox", "other"])
def test_captain_settings_written_under_another_temp_folder_add_no_line(
    state: Path, tmp_path: Path, setup_temp: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    other = tmp_path / "setup-temp"
    other.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(launch.sandbox_tmp()[0] if setup_temp == "sandbox" else other))
    installed(written())
    monkeypatch.setattr(tempfile, "tempdir", None)
    assert output([], capsys) == "engine: not running\n"


@pytest.mark.parametrize("settings", [{"permissions": {"allow": ["Read"]}}, "{not json"])
def test_out_of_date_captain_settings_say_to_run_setup(
    state: Path, settings: dict | str, capsys: pytest.CaptureFixture[str]
):
    installed(settings)
    assert output([], capsys) == f"engine: not running\n{captain.OUTDATED}\n"
    assert "/deckhand:setup" in captain.OUTDATED


def test_no_installed_captain_settings_add_no_line(state: Path, capsys: pytest.CaptureFixture[str]):
    assert output([], capsys) == "engine: not running\n"
