from __future__ import annotations

from pathlib import Path

from deckhand.policy import STAGES, Policy
from deckhand.step import Refusal
from deckhand.tiers import Tier

CHAOS_LEVEL = 3
OWN = Path(__file__).resolve().parent.parent / "skills" / "story" / "lenses"
PROJECT = Path(".claude") / "deckhand" / "lenses"

PLAN = ("contract", "correctness", "red-team", "tests", "input-shape")
BRANCH = ("correctness", "tests", "idiomatic", "sibling-form")


def lenses_for(stage: str, tier: Tier, policy: Policy) -> list[str]:
    if stage not in STAGES:
        raise Refusal(f"the review stage {stage!r} is not plan or branch")
    if stage == "plan":
        found = list(PLAN)
        if tier.level >= CHAOS_LEVEL:
            found.append("chaos")
    else:
        found = list(BRANCH)
        if policy.front_end:
            found.append("impeccable")
    found += [lens for lens in policy.extra_lenses.get(stage, []) if lens not in found]
    return found


def path_of(name: str, repo: Path) -> Path:
    for path in (OWN / f"{name}.md", repo / PROJECT / f"{name}.md"):
        if path.is_file():
            return path
    raise Refusal(f"the lens {name} has no file in {OWN} or {repo / PROJECT}")
