from __future__ import annotations

import re
from pathlib import Path

import pytest

from deckhand.cli import _load_steps
from deckhand.step import REGISTRY

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


def test_feature_skill_names_only_registered_commands() -> None:
    _, body = split(SKILLS / "feature" / "SKILL.md")
    _load_steps()
    named = re.findall(r"`deckhand (\w+)", body)
    assert named
    assert set(named) <= set(REGISTRY)
