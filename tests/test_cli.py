from __future__ import annotations

import json
import tomllib

import pytest

from deckhand import __version__
from deckhand.cli import main
from deckhand.step import REGISTRY, Refusal, register
from tests.conftest import ROOT


@pytest.fixture
def registry():
    original = dict(REGISTRY)
    try:
        yield REGISTRY
    finally:
        REGISTRY.clear()
        REGISTRY.update(original)


def test_version_matches_every_manifest():
    """A release bumps the version wherever it is written; this is the check that keeps them together."""
    manifests = ROOT / ".claude-plugin"
    manifest = json.loads((ROOT / ".release-please-manifest.json").read_text(encoding="utf-8"))
    versions = {
        "pyproject.toml": tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "plugin.json": json.loads((manifests / "plugin.json").read_text(encoding="utf-8"))["version"],
        "release-please manifest": manifest["."],
        "uv.lock": next(
            package["version"]
            for package in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
            if package["name"] == "deckhand"
        ),
    }
    assert all(found == __version__ for found in versions.values()), f"__version__ is {__version__}; {versions}"


def test_the_marketplace_entry_carries_no_version():
    """Claude Code reads plugin.json's version; a second copy in the entry is what validate warns about."""
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert "version" not in marketplace["metadata"]
    assert "version" not in marketplace["plugins"][0]


def test_unknown_command_exits_2_and_names_it(capsys):
    assert main(["nope"]) == 2
    assert "nope" in capsys.readouterr().err


def test_refusal_prints_rule_and_exits_1(registry, capsys):
    @register("probe")
    class Probe:
        def context(self, args):
            return None

        def apply(self, args):
            raise Refusal("no plan")

    assert main(["probe", "apply"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "deckhand: no plan\n"
    assert captured.out == ""


def test_context_never_raises(registry, capsys):
    @register("probe")
    class Probe:
        def context(self, args):
            raise KeyError("missing")

        def apply(self, args):
            return None

    assert main(["probe", "context"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert "probe" in lines[0]


def test_unknown_verb_exits_2_and_names_the_choices(registry, capsys):
    @register("probe")
    class Probe:
        def context(self, args):
            return None

        def apply(self, args):
            return None

    assert main(["probe", "bogus"]) == 2
    assert "context, apply" in capsys.readouterr().err
    assert main(["probe"]) == 2
    assert "context, apply" in capsys.readouterr().err


def test_apply_success_exits_0_with_its_args(registry):
    seen = []

    @register("probe")
    class Probe:
        def context(self, args):
            return None

        def apply(self, args):
            seen.append(args)

    assert main(["probe", "apply", "7"]) == 0
    assert seen == [["7"]]


def test_context_success_exits_0_and_passes_stdout_through(registry, capsys):
    @register("probe")
    class Probe:
        def context(self, args):
            print("briefing", *args)

        def apply(self, args):
            return None

    assert main(["probe", "context", "7"]) == 0
    assert capsys.readouterr().out == "briefing 7\n"


def test_registering_a_name_twice_raises(registry):
    @register("probe")
    class First:
        pass

    with pytest.raises(ValueError, match="command registered twice: probe"):

        @register("probe")
        class Second:
            pass
