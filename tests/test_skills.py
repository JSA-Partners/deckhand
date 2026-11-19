from __future__ import annotations

import inspect
import re
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from deckhand import decisions
from deckhand.cli import _load_steps
from deckhand.step import REGISTRY, VERBS, Refusal

SKILLS = Path(__file__).resolve().parent.parent / "skills"
FILES = sorted(SKILLS.glob("*/SKILL.md"))
INJECTION = re.compile(r"^!`", re.MULTILINE)


def split(path: Path) -> tuple[dict[str, str], str]:
    _, head, body = path.read_text().split("---\n", 2)
    fields = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields, body


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_frontmatter_and_one_injection(path: Path) -> None:
    fields, body = split(path)
    assert fields["name"] == path.parent.name
    assert fields["description"]
    assert len(INJECTION.findall(body)) == 1


INJECTED = re.compile(r"^!`deckhand ([^\s`]+) ([^\s`]+)", re.MULTILINE)
UNPORTED = {"captain"}


@pytest.mark.parametrize("path", [p for p in FILES if p.parent.name not in UNPORTED], ids=lambda p: p.parent.name)
def test_injection_runs_a_registered_command(path: Path) -> None:
    _load_steps()
    _, body = split(path)
    [(command, verb)] = INJECTED.findall(body)
    assert command in REGISTRY, command
    assert verb in VERBS, f"{command} {verb}"


STEP_SKILLS = ("feature", "story", "signoff")


@pytest.mark.parametrize("name", STEP_SKILLS)
def test_step_skill_exists_and_stays_short(name: str) -> None:
    path = SKILLS / name / "SKILL.md"
    assert path.exists()
    _, body = split(path)
    assert len(body.split()) < 400


def named_commands(body: str) -> list[list[str]]:
    prose = re.sub(r"^!`.*$", "", body, flags=re.MULTILINE)
    spans = re.findall(r"`(deckhand [^`]*)`", " ".join(prose.split()))
    return [shlex.split(re.sub(r"[\[\]]|\.\.\.", "", span))[1:] for span in spans]


def parser(module: ModuleType) -> Callable[[list[str]], dict]:
    parse = getattr(module, "_parse", None) or module.parse
    if len(inspect.signature(parse).parameters) == 2:
        return lambda args: parse(args, module.USAGE)
    return parse


def known_words(name: str, module: ModuleType) -> tuple[str, ...] | None:
    return decisions.KINDS if name == "decide" else getattr(module, "ACTIONS", None)


@pytest.mark.parametrize("name", STEP_SKILLS)
def test_step_skill_commands_parse_as_written(name: str) -> None:
    _load_steps()
    _, body = split(SKILLS / name / "SKILL.md")
    commands = named_commands(body)
    assert commands
    for tokens in commands:
        command, verb, *args = tokens
        assert command in REGISTRY, command
        assert verb in VERBS, f"{command} {verb}"
        assert hasattr(REGISTRY[command], verb)
        module = sys.modules[type(REGISTRY[command]).__module__]
        try:
            words = parser(module)(args).get("words", [])
        except Refusal as refusal:
            pytest.fail(f"deckhand {' '.join(tokens)}: {refusal}")
        known = known_words(command, module)
        if known is not None:
            for word in (w for w in words if w.islower()):
                assert word in known, f"deckhand {' '.join(tokens)}: {word}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.parent.name)
def test_a_skill_that_names_deckhand_commands_may_run_them(path: Path) -> None:
    fields, body = split(path)
    if not named_commands(body):
        return
    allowed = fields["allowed-tools"].split()
    assert "Bash" in allowed or "Bash(deckhand *)" in fields["allowed-tools"]
