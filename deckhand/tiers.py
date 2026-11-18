from __future__ import annotations

from dataclasses import dataclass

from deckhand.step import Refusal


@dataclass(frozen=True)
class Fact:
    key: str
    question: str
    tier: int


@dataclass(frozen=True)
class Tier:
    level: int
    contract: bool
    data_rule: bool
    tools_key: str


FACTS: tuple[Fact, ...] = (
    Fact(
        "data_rule",
        "Does this story enforce a data rule: ownership, permissions, uniqueness or money?",
        1,
    ),
    Fact(
        "multi_step",
        "Does this story add a flow or an entity with several states?",
        2,
    ),
    Fact(
        "concurrency",
        "Does this story involve jobs, retries, webhooks, queues or two writers?",
        3,
    ),
    Fact(
        "pure_algorithm",
        "Does this story add a costly pure algorithm that should be proven in Lean?",
        4,
    ),
    Fact(
        "crosses_repositories",
        "Does this story change a contract that another repository relies on?",
        0,
    ),
)

_BY_KEY = {fact.key: fact for fact in FACTS}


def tier(chosen: set[str]) -> Tier:
    facts = []
    for key in sorted(chosen):
        if key not in _BY_KEY:
            raise Refusal(f"unknown fact {key}")
        facts.append(_BY_KEY[key])
    level = max((fact.tier for fact in facts), default=0)
    contract = any(fact.key == "crosses_repositories" for fact in facts)
    data_rule = any(fact.key == "data_rule" for fact in facts)
    return Tier(level=level, contract=contract, data_rule=data_rule, tools_key=str(level))


NONE = "None of these"
OPTIONS = [*(fact.question for fact in FACTS), NONE]


def chosen(answer: str) -> set[str]:
    labels = []
    rest = answer
    while rest:
        label = next((o for o in OPTIONS if rest == o or rest.startswith(o + ", ")), None)
        if label is None:
            raise Refusal(f"the tier answer {answer!r} is not a selection of the fact questions")
        labels.append(label)
        rest = rest[len(label) + 2 :]
    if NONE in labels and len(labels) > 1:
        raise Refusal(f"{NONE} cannot be chosen with a fact")
    return {fact.key for fact in FACTS if fact.question in labels}


def from_facts(value: object) -> Tier | None:
    if not isinstance(value, list) or not all(isinstance(key, str) and key in _BY_KEY for key in value):
        return None
    return tier(set(value))
