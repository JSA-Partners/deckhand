from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest

from deckhand import answers, config, engine, tui
from deckhand.engine import Linked
from deckhand.step import Refusal
from deckhand.steps.engine import Engine, command, invocation
from deckhand.store.local import default_path
from deckhand.store.state import LocalState
from tests.shell import Shell

NOW = 1795000000.0


def context(capsys: pytest.CaptureFixture[str]) -> list[str]:
    Engine(Shell(), clock=lambda: NOW).context([])
    return capsys.readouterr().out.splitlines()


def test_context_says_running_with_a_fresh_heartbeat_and_not_running_with_a_stale_one(capsys):
    with LocalState(default_path()) as rows:
        rows.beat(4242, NOW - 10)
    assert context(capsys) == ["engine: running (pid 4242)", f"start: {command()}"]
    with LocalState(default_path()) as rows:
        rows.beat(4242, NOW - 31)
    assert context(capsys) == ["engine: not running", f"start: {command()}"]


def test_context_with_no_heartbeat_says_not_running(capsys):
    assert context(capsys)[0] == "engine: not running"


def test_context_with_a_malformed_heartbeat_says_not_running(capsys):
    with LocalState(default_path()) as rows:
        rows.put("heartbeat", {"pid": 1})
    assert context(capsys)[0] == "engine: not running"


def test_copy_pipes_the_start_command_to_pbcopy(capsys, monkeypatch: pytest.MonkeyPatch):
    piped = []
    shell = Shell()
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pbcopy")

    def runner(argv, cwd=None, stdin=None):
        piped.append(stdin)
        return shell(argv, cwd, stdin)

    Engine(runner).apply(["copy"])
    assert [argv for argv, _ in shell.calls] == [["pbcopy"]] and piped == [command()]
    assert capsys.readouterr().out == f"copied: {command()}\n"


def test_copy_without_pbcopy_prints_the_command(capsys, monkeypatch: pytest.MonkeyPatch):
    shell = Shell()
    monkeypatch.setattr(shutil, "which", lambda name: None)
    Engine(shell).apply(["copy"])
    assert shell.calls == [] and capsys.readouterr().out == f"copy this: {command()}\n"


def tty(monkeypatch: pytest.MonkeyPatch, terminal: bool = True) -> None:
    monkeypatch.setattr(sys.stdout, "isatty", lambda: terminal)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: terminal)


def run(monkeypatch: pytest.MonkeyPatch, watch, shell: Shell | None = None) -> Shell:
    shell = shell or Shell()
    tty(monkeypatch)
    monkeypatch.setattr(engine, "link", lambda team, *rest: Linked(None, None, NOW, "network: down"))
    monkeypatch.setattr(tui, "watch", watch)
    Engine(shell, clock=lambda: NOW).apply(["run", "--team-key", "ACME"])
    return shell


def test_run_refuses_without_a_terminal(monkeypatch: pytest.MonkeyPatch):
    tty(monkeypatch, terminal=False)
    with pytest.raises(Refusal, match="^the engine needs its own terminal tab$"):
        Engine(Shell()).apply(["run", "--team-key", "ACME"])
    assert not (answers.state_dir() / engine.LOCK).exists()


def test_run_refuses_a_second_engine_naming_the_pid(monkeypatch: pytest.MonkeyPatch):
    tty(monkeypatch)
    held = engine.lock(answers.state_dir())
    with LocalState(default_path()) as rows:
        rows.beat(4242, NOW - 100)
    try:
        with pytest.raises(Refusal, match=r"^the engine already runs in another tab \(pid 4242\)$"):
            Engine(Shell(), clock=lambda: NOW).apply(["run", "--team-key", "ACME"])
        with LocalState(default_path()) as rows:
            assert rows.heartbeat() == (4242, NOW - 100)
    finally:
        assert held is not None
        held.close()


def test_a_second_engine_without_a_heartbeat_names_the_pid_in_the_lock(monkeypatch: pytest.MonkeyPatch):
    tty(monkeypatch)
    held = engine.lock(answers.state_dir())
    try:
        with pytest.raises(Refusal, match=rf"\(pid {os.getpid()}\)$"):
            Engine(Shell(), clock=lambda: NOW).apply(["run", "--team-key", "ACME"])
    finally:
        assert held is not None
        held.close()


def test_run_holds_the_lock_while_the_screen_runs_and_drops_the_heartbeat_after(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    seen = {}

    def watch(read_status, poller, clock, color, g, running):
        seen["locked"] = engine.lock(answers.state_dir()) is None
        running.tick()
        with LocalState(default_path()) as rows:
            seen["beat"] = rows.heartbeat()
        raise RuntimeError("screen")

    with pytest.raises(RuntimeError):
        run(monkeypatch, watch)
    assert seen["locked"] and seen["beat"] == (os.getpid(), NOW)
    with LocalState(default_path()) as rows:
        assert rows.heartbeat() is None
    assert capsys.readouterr().out == ""


def test_run_prints_what_stopping_the_hands_left_after_the_screen(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    run(monkeypatch, lambda *args: ["ACME-1: error: one\nfatal: \x1b[2Jtwo"])
    assert capsys.readouterr().out == "ACME-1: error: one fatal: [2Jtwo\n"


def test_leaving_says_it_finishes_an_action_in_flight(monkeypatch: pytest.MonkeyPatch, capsys):
    def watch(read_status, poller, clock, color, g, running):
        monkeypatch.setattr(running, "finishing", lambda: True)
        return []

    run(monkeypatch, watch)
    assert capsys.readouterr().out == "finishing an action\n"


@pytest.mark.parametrize("args", [[], ["start"], ["run", "copy"], ["--json"]])
def test_apply_takes_run_or_copy(args: list[str]):
    with pytest.raises(Refusal):
        Engine(Shell()).apply(args)


def test_the_start_command_carries_the_folders_like_the_board_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    launcher = config.config_dir() / "deckhand"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o700)
    places = [f"DECKHAND_CONFIG_DIR={config.config_dir()}", f"DECKHAND_STATE_DIR={answers.state_dir()}"]
    assert command() == shlex.join(["env", *places, str(launcher), "engine", "apply", "run"])
    assert command() == invocation("engine", "apply", "run")


def test_the_start_command_carries_no_folders_at_their_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DECKHAND_CONFIG_DIR", raising=False)
    monkeypatch.delenv("DECKHAND_STATE_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["/opt/deckhand/bin/deckhand"])
    assert command() == "/opt/deckhand/bin/deckhand engine apply run"


def test_without_a_launcher_the_start_command_names_this_deckhand(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    program = tmp_path / "plug in" / "bin" / "deckhand"
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "argv", [str(program.parent / ".." / "bin" / "deckhand"), "engine", "apply"])
    assert command().endswith(f" {shlex.quote(str(program.resolve()))} engine apply run")
