from __future__ import annotations

from dataclasses import dataclass

from deckhand.step import Refusal


@dataclass(frozen=True)
class Tier:
    fact: str
    label: str
    level: int

    @property
    def data_rule(self) -> bool:
        return self.fact == "data_rule"


TIERS: tuple[Tier, ...] = (
    Tier("concurrency", "Jobs, retries, webhooks, queues or two writers", 3),
    Tier("multi_step", "A flow or an entity with several states", 2),
    Tier("data_rule", "A data rule: ownership, permissions, uniqueness or money", 1),
    Tier("none", "None of these", 0),
)

NONE = TIERS[-1].label
OPTIONS = [tier.label for tier in TIERS]
_BY_FACT = {tier.fact: tier for tier in TIERS}


def chosen(answer: str) -> Tier:
    found = next((tier for tier in TIERS if tier.label == answer), None)
    if found is None:
        raise Refusal(f"the tier answer {answer!r} is not one of the offered options")
    return found


def from_record(data: dict) -> Tier | None:
    fact = data.get("fact")
    found = _BY_FACT.get(fact) if isinstance(fact, str) else None
    return found if found is not None and data.get("level") == found.level else None
