from __future__ import annotations

from dataclasses import dataclass

from deckhand.records import Record


@dataclass(frozen=True)
class Tier:
    fact: str
    label: str
    level: int


TIERS: tuple[Tier, ...] = (
    Tier("concurrency", "Jobs, retries, webhooks, queues or two writers", 3),
    Tier("multi_step", "A flow or an entity with several states", 2),
    Tier("data_rule", "A data rule: ownership, permissions, uniqueness or money", 1),
    Tier("none", "None of these", 0),
)

FACTS = tuple(tier.fact for tier in TIERS)
_BY_FACT = {tier.fact: tier for tier in TIERS}


def named(fact: object) -> Tier | None:
    return _BY_FACT.get(fact) if isinstance(fact, str) else None


def from_record(data: dict) -> Tier | None:
    found = named(data.get("fact"))
    return found if found is not None and data.get("level") == found.level else None


LEAN = 4


def tools(tier_tools: dict[int, list[str]], level: int, records: list[Record], story: str) -> list[str]:
    found = list(tier_tools.get(level, []))
    if any(r.kind == "lean" and r.subject == story for r in records):
        found += [tool for tool in tier_tools.get(LEAN, []) if tool not in found]
    return found
