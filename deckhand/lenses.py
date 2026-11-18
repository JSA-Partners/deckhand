from __future__ import annotations

from deckhand.policy import Policy
from deckhand.tiers import Tier

CHAOS_LEVEL = 3

PLAN = ("contract", "correctness", "red-team", "tests", "input-shape")
BRANCH = ("correctness", "tests", "idiomatic", "sibling-form")


def lenses_for(stage: str, tier: Tier, policy: Policy) -> list[str]:
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
