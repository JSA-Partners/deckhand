from __future__ import annotations

from pathlib import Path

import pytest

from deckhand.lenses import BRANCH, PLAN, lenses_for
from deckhand.policy import Policy
from deckhand.tiers import TIERS

LENSES = Path(__file__).resolve().parent.parent / "skills" / "story" / "lenses"
KEYS = ("lens", "severity", "path", "line", "summary", "fix", "evidence")
NONE, DATA_RULE, MULTI_STEP, CONCURRENCY = (TIERS[3], TIERS[2], TIERS[1], TIERS[0])


def policy(**fields) -> Policy:
    return Policy(repository_label="acme-widgets", **fields)


def test_plan_core_five() -> None:
    assert lenses_for("plan", MULTI_STEP, policy()) == [
        "contract",
        "correctness",
        "red-team",
        "tests",
        "input-shape",
    ]


def test_tier_3_adds_chaos() -> None:
    assert lenses_for("plan", CONCURRENCY, policy())[-1] == "chaos"
    assert "chaos" not in lenses_for("plan", MULTI_STEP, policy())
    assert "chaos" not in lenses_for("branch", CONCURRENCY, policy())


def test_front_end_adds_impeccable() -> None:
    assert "impeccable" in lenses_for("branch", NONE, policy(front_end=True))
    assert "impeccable" not in lenses_for("branch", NONE, policy())
    assert "impeccable" not in lenses_for("plan", NONE, policy(front_end=True))


def test_extra_lenses_from_policy() -> None:
    extra = policy(extra_lenses={"branch": ["go-style"], "plan": ["tests"]})
    assert lenses_for("branch", NONE, extra) == [*BRANCH, "go-style"]
    assert lenses_for("plan", NONE, extra) == list(PLAN)


def frontmatter(path: Path) -> dict[str, str]:
    _, head, _ = path.read_text().split("---\n", 2)
    return {k.strip(): v.strip() for k, _, v in (line.partition(":") for line in head.splitlines())}


@pytest.mark.parametrize("name", sorted({*PLAN, *BRANCH, "chaos", "impeccable"}))
def test_lens_file_names_the_contract(name: str) -> None:
    path = LENSES / f"{name}.md"
    text = path.read_text()
    fields = frontmatter(path)
    assert fields["name"] == name
    stages = {s.strip() for s in fields["stage"].split(",")}
    assert stages <= {"plan", "branch"}
    assert (name in PLAN or name == "chaos") == ("plan" in stages)
    assert (name in BRANCH or name == "impeccable") == ("branch" in stages)
    assert all(f'"{key}"' in text for key in KEYS)
    assert "[]" in text
    assert text.isascii()
    assert len(text.split()) < 300
