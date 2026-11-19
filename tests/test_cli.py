from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib

import pytest

from deckhand import __version__
from deckhand.cli import _load_steps, main
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


def test_broken_step_module_does_not_stop_other_commands(tmp_path, monkeypatch, capsys, registry):
    from deckhand import steps

    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n")
    monkeypatch.setattr(steps, "__path__", [*steps.__path__, str(tmp_path)])
    assert main(["nonesuch"]) == 2
    err = capsys.readouterr().err
    assert "cannot load step broken: boom" in err
    assert "Traceback" not in err
    assert "setup" in REGISTRY


SECRET = "lin_api_secretvalue"


def test_context_failure_prints_only_the_type_and_command(registry, capsys):
    @register("probe")
    class Probe:
        def context(self, args):
            raise ValueError(f"Invalid header value b'{SECRET}'")

        def apply(self, args):
            return None

    assert main(["probe", "context"]) == 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert captured.out == "deckhand: probe context could not be read: ValueError\n"


def test_context_prints_refusal_rule(registry, capsys):
    @register("probe")
    class Probe:
        def context(self, args):
            raise Refusal("no team ACME")

        def apply(self, args):
            return None

    assert main(["probe", "context"]) == 0
    assert capsys.readouterr().out == "deckhand: probe context: no team ACME\n"


def test_context_hides_other_exceptions(registry, capsys):
    @register("probe")
    class Probe:
        def context(self, args):
            raise ValueError("secret-text")

        def apply(self, args):
            return None

    assert main(["probe", "context"]) == 0
    captured = capsys.readouterr()
    assert "secret-text" not in captured.out + captured.err


def test_apply_unexpected_error_prints_only_the_type_and_exits_1(registry, capsys):
    @register("probe")
    class Probe:
        def context(self, args):
            return None

        def apply(self, args):
            raise UnicodeDecodeError("utf-8", SECRET.encode(), 0, 1, "invalid start byte")

    assert main(["probe", "apply"]) == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert "Traceback" not in captured.err
    assert captured.err == "deckhand: probe apply failed: UnicodeDecodeError\n"


def test_apply_linear_error_prints_code_and_message_and_exits_1(registry, capsys):
    from deckhand.linear.client import LinearError

    @register("probe")
    class Probe:
        def context(self, args):
            return None

        def apply(self, args):
            raise LinearError("network", "could not reach Linear")

    assert main(["probe", "apply"]) == 1
    assert capsys.readouterr().err == "deckhand: network: could not reach Linear\n"


def test_apply_git_error_prints_command_code_and_tail_and_exits_1(registry, capsys):
    from deckhand.git import GitError

    @register("probe")
    class Probe:
        def context(self, args):
            return None

        def apply(self, args):
            raise GitError("git merge-base", 128, "fatal: no such ref")

    assert main(["probe", "apply"]) == 1
    assert capsys.readouterr().err == "deckhand: probe failed (exit 128): fatal: no such ref\n"


def test_apply_header_unfit_token_prints_a_fixed_line_without_it(registry, capsys):
    import http.client
    import json

    from deckhand.linear.client import LinearClient

    def validating(request):
        connection = http.client.HTTPConnection("localhost")
        connection.putrequest("POST", "/graphql")
        for name, value in request.header_items():
            connection.putheader(name, value)
        return json.dumps({"data": {}}).encode()

    @register("probe")
    class Probe:
        def context(self, args):
            return None

        def apply(self, args):
            LinearClient(lambda: f"{SECRET}\n{SECRET}", lambda: None, validating).query("q", {})

    assert main(["probe", "apply"]) == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert captured.err == "deckhand: credentials: the credential cannot be sent as a header\n"


def test_context_git_error_prints_its_reason_at_exit_0(registry, capsys):
    from deckhand.git import GitError, stderr_tail

    @register("probe")
    class Probe:
        def context(self, args):
            raise GitError("git diff", 128, stderr_tail(f"fatal: bad revision\nremote: {SECRET}"))

        def apply(self, args):
            return None

    assert main(["probe", "context"]) == 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert (
        captured.out == "deckhand: probe context: git diff failed (exit 128): fatal: bad revision\nremote: [redacted]\n"
    )


class Recorder:
    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    def context(self, args):
        return None

    def apply(self, args):
        self.seen.append(args)


@pytest.fixture
def recorders(registry):
    _load_steps()
    found: dict[str, Recorder] = {}
    for name in ("setup", "feature", "stories", "hand", "decide", "signoff", "hook", "draft", "build", "ask", "spike"):
        found[name] = registry[name] = Recorder()
    return found


def refused(argv: list[str], capsys) -> bool:
    code = main(argv)
    err = capsys.readouterr().err
    return code == 1 and err == f"deckhand: a hand cannot run {argv[0]} apply; leave it for the person's captain\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["setup", "apply", "--team-key", "ACME"],
        ["feature", "apply", "new", "--name", "N"],
        ["stories", "apply", "--file", "map.json"],
        ["hand", "apply", "start", "ACME-1"],
        ["nonesuch", "apply", "ACME-1"],
        ["decide", "apply", "nonce"],
        ["signoff", "apply", "ACME-1", "review"],
        ["signoff", "apply", "ACME-1", "--team-key", "ACME", "summary"],
        ["hook", "apply", "answer"],
        ["draft", "apply", "ACME-1", "take"],
        ["draft", "apply", "ACME-1", "--file", "d.md", "take"],
    ],
)
def test_hand_role_refuses_person_applies(argv, recorders, monkeypatch, capsys):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    assert refused(argv, capsys)
    assert all(not r.seen for r in recorders.values())


def test_hand_role_refuses_another_story(recorders, monkeypatch, capsys):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    assert refused(["build", "apply", "ACME-2", "push"], capsys)
    assert refused(["build", "apply", "--team-key", "ACME-1", "ACME-2", "push"], capsys)
    monkeypatch.setenv("DECKHAND_STORY", "")
    assert refused(["build", "apply", "", "push"], capsys)
    assert recorders["build"].seen == []
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    assert main(["build", "apply", "ACME-1", "push"]) == 0
    assert main(["signoff", "apply", "ACME-1", "summary", "--summary", "s.json"]) == 0
    assert main(["draft", "apply", "ACME-1", "--file", "d.md"]) == 0
    assert main(["hook", "apply", "session"]) == 0
    assert main(["build", "context", "ACME-2"]) == 0
    assert recorders["build"].seen == [["ACME-1", "push"]]
    assert recorders["signoff"].seen and recorders["draft"].seen and recorders["hook"].seen


def test_spike_hand_cannot_ask(recorders, monkeypatch, capsys):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_SPIKE", "0123456789ab")
    assert refused(["ask", "apply", "0123456789ab", "--file", "q.json"], capsys)
    assert refused(["build", "apply", "0123456789ab", "push"], capsys)
    assert refused(["spike", "apply", "ffffffffffff", "--file", "a.md"], capsys)
    assert main(["spike", "apply", "0123456789ab", "--file", "a.md"]) == 0
    assert main(["hook", "apply", "permission"]) == 0
    assert recorders["ask"].seen == [] and recorders["build"].seen == []
    assert recorders["spike"].seen == [["0123456789ab", "--file", "a.md"]]


def test_a_story_hand_cannot_answer_a_spike(recorders, monkeypatch, capsys):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    assert refused(["spike", "apply", "ACME-1", "--file", "a.md"], capsys)
    assert recorders["spike"].seen == []


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not on PATH")
@pytest.mark.parametrize("planted", [".python-version", "uv.toml"])
def test_the_entry_point_ignores_a_planted_interpreter_and_uv_config(planted, tmp_path):
    plugin = tmp_path / "plugin"
    shutil.copytree(ROOT / "bin", plugin / "bin")
    shutil.copytree(ROOT / "deckhand", plugin / "deckhand", ignore=shutil.ignore_patterns("__pycache__"))
    work = tmp_path / "work"
    work.mkdir()
    marker = tmp_path / "marker"
    python = tmp_path / "python"
    python.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    python.chmod(0o755)
    text = {".python-version": f"{python}\n", "uv.toml": "this is not toml\n"}[planted]
    (work / planted).write_text(text)
    outside = {k: v for k, v in os.environ.items() if not k.startswith("UV") and k != "VIRTUAL_ENV"}
    fresh = {**outside, "UV_CACHE_DIR": str(tmp_path / "cache")}
    done = subprocess.run(
        [os.path.join("..", "plugin", "bin", "deckhand"), "nonesuch"],
        cwd=work,
        env=fresh,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert not marker.exists()
    assert (done.returncode, done.stderr) == (2, "deckhand: unknown command 'nonesuch'\n")
