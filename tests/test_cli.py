from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from deckhand import __version__, cli
from deckhand.cli import command
from tests.conftest import ROOT, run_deckhand


def test_version_prints_the_package_version():
    result = run_deckhand("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == f"deckhand {__version__}"


def test_every_version_source_agrees():
    """A release bumps five strings at once; this is the check the release rule in CLAUDE.md relies on."""
    manifests = ROOT / ".claude-plugin"
    marketplace = json.loads((manifests / "marketplace.json").read_text(encoding="utf-8"))
    versions = {
        "pyproject.toml": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "plugin.json": json.loads((manifests / "plugin.json").read_text(encoding="utf-8"))["version"],
        "marketplace.json metadata": marketplace["metadata"]["version"],
        "marketplace.json plugin": marketplace["plugins"][0]["version"],
    }
    assert all(found == __version__ for found in versions.values()), f"__version__ is {__version__}; {versions}"


def test_unknown_command_is_a_usage_error():
    result = run_deckhand("bogus")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_handler_error_prints_one_line_to_stderr(registry, capsys):
    @command("probe", lambda parser: None)
    def probe(args):
        """Probe command for tests."""
        raise ValueError("something broke")

    result = cli.main(["probe"])

    assert result == 1
    assert capsys.readouterr().err == "deckhand probe: something broke\n"


def test_handler_keyboard_interrupt_returns_130(registry):
    @command("probe", lambda parser: None)
    def probe(args):
        """Probe command for tests."""
        raise KeyboardInterrupt

    assert cli.main(["probe"]) == 130


def test_handler_system_exit_propagates(registry):
    @command("probe", lambda parser: None)
    def probe(args):
        """Probe command for tests."""
        raise SystemExit(7)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["probe"])
    assert excinfo.value.code == 7


def test_no_arguments_exits_2():
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2


def test_command_help_is_docstrings_first_line(registry, capsys):
    @command("probe", lambda parser: None)
    def probe(args):
        """First line of help.

        More detail that only shows up in the full description.
        """
        return 0

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "First line of help." in capsys.readouterr().out


def test_flooding_stdout_into_head_does_not_error(tmp_path):
    script = tmp_path / "flood.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from deckhand.cli import command, main\n"
        "\n"
        "@command('flood', lambda parser: None)\n"
        "def flood(args):\n"
        '    """Flood stdout."""\n'
        "    for i in range(200000):\n"
        "        print(i)\n"
        "    return 0\n"
        "\n"
        "sys.exit(main(['flood']))\n"
    )
    result = subprocess.run(
        ["bash", "-c", f'"{sys.executable}" "{script}" | head -2'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == ""


def _raising_launcher(tmp_path) -> Path:
    """A one-command deckhand whose handler raises with its argument in the message."""
    script = tmp_path / "raiser.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from deckhand.cli import command, main\n"
        "\n"
        "@command('raise-it', lambda parser: parser.add_argument('text'))\n"
        "def raise_it(args):\n"
        '    """Raise with the argument."""\n'
        "    raise ValueError(f'bad name {args.text}')\n"
        "\n"
        "sys.exit(main(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    return script


def test_handler_error_prints_utf8_to_stderr_under_ascii_encoding(tmp_path):
    script = _raising_launcher(tmp_path)

    result = subprocess.run(
        [sys.executable, str(script), "raise-it", "✓✓"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
    )

    assert result.returncode == 1
    assert result.stderr == "deckhand raise-it: bad name ✓✓\n"


def test_handler_error_with_unencodable_argv_is_still_one_line(tmp_path):
    script = _raising_launcher(tmp_path)

    result = subprocess.run(
        [sys.executable, str(script), "raise-it", os.fsdecode(b"\xff")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("deckhand raise-it: bad name ")


def _verbs(parser: argparse.ArgumentParser) -> list[str]:
    """The verbs one command answers to, in name order; none at all for a plain command."""
    verbs = next((a for a in parser._actions if getattr(a, "choices", None)), None)
    return sorted(verbs.choices) if verbs is not None else []


def test_the_command_surface_is_exactly_the_steps_and_their_verbs():
    """Two verbs for a step that writes what it validated; one for a step whose write is elsewhere."""
    commands = next(a for a in cli.build_parser()._actions if getattr(a, "choices", None)).choices

    assert {name: _verbs(sub) for name, sub in commands.items()} == {
        "amend": ["apply", "context"],
        "log": [],
        "commit": ["context"],
        "document": ["context"],
        "finish": ["apply", "context"],
        "new": ["apply", "context"],
        "next": ["context"],
        "ready": ["apply", "context"],
        "review": ["apply", "context"],
        "setup": ["apply", "context"],
        "start": ["apply", "context"],
    }


# A step runs from `next` and nowhere else, so the line that ends a command never sends a person to
# one; the commands they type are the whole of what a `Next:` line may name.
_STEP_COMMAND = re.compile(r"/deckhand:(amend|finish|ready|review|start)\b")


def test_no_printed_next_line_names_a_step_command():
    """Every `Next:` line in the package, read the way a person reads it on the screen."""
    offenders = [
        f"{path.name}:{number}: {line.strip()}"
        for path in sorted((ROOT / "deckhand").glob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if "Next:" in line and _STEP_COMMAND.search(line)
    ]

    assert offenders == []
