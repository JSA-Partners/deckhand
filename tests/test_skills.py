from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest

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


def test_feature_skill_exists() -> None:
    assert (SKILLS / "feature" / "SKILL.md").exists()


def test_feature_skill_stays_short() -> None:
    _, body = split(SKILLS / "feature" / "SKILL.md")
    assert len(body.split()) < 400


def named_commands(body: str) -> list[list[str]]:
    spans = re.findall(r"`(deckhand [^`]*)`", " ".join(body.split()))
    return [shlex.split(re.sub(r"[\[\]]|\.\.\.", "", span))[1:] for span in spans]


def test_feature_skill_commands_parse_as_written() -> None:
    _load_steps()
    _, body = split(SKILLS / "feature" / "SKILL.md")
    commands = named_commands(body)
    assert commands
    for tokens in commands:
        name, verb, *args = tokens
        assert name in REGISTRY, name
        assert verb in VERBS, f"{name} {verb}"
        assert hasattr(REGISTRY[name], verb)
        parse = sys.modules[type(REGISTRY[name]).__module__]._parse
        try:
            parse(args)
        except Refusal as refusal:
            pytest.fail(f"deckhand {' '.join(tokens)}: {refusal}")
