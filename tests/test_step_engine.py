from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path

import pytest

from deckhand import answers, config
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


def test_run_waits_for_the_screen():
    with pytest.raises(Refusal, match="the engine's screen is not built yet"):
        Engine(Shell()).apply(["run"])


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
