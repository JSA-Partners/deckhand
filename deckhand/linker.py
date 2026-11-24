from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from deckhand import config, fleet, policy, reading
from deckhand.linear.client import LinearError
from deckhand.policy import Policy
from deckhand.reading import Reading
from deckhand.step import Refusal
from deckhand.store.open import Stores
from deckhand.worker import problem

NO_FLEET = "the fleet is not read yet"
REFRESH = 600.0


@dataclass(frozen=True)
class Linked:
    stores: Stores | None
    reading: Reading | None
    at: float
    error: str
    local: config.LocalConfig = field(default_factory=config.LocalConfig)
    policies: Mapping[Path, Policy] = field(default_factory=dict)
    problems: tuple[str, ...] = ()
    labels: Mapping[str, list[str]] = field(default_factory=dict)
    broken: Mapping[Path, str] = field(default_factory=dict)


@dataclass
class Kept:
    teams: dict[str, str] = field(default_factory=dict)
    labels: dict[str, list[str]] = field(default_factory=dict)
    at: float = float("-inf")

    def refresh(self, now: float) -> None:
        if now - self.at >= REFRESH:
            self.teams, self.labels, self.at = {}, {}, now


def _configured(problems: list[str]) -> tuple[config.LocalConfig, dict[Path, Policy], dict[Path, str]]:
    try:
        local = config.local()
    except Refusal as error:
        problems.append(error.rule)
        local = config.LocalConfig()
    policies: dict[Path, Policy] = {}
    broken: dict[Path, str] = {}
    for clone in local.clones.values():
        try:
            policies[clone] = Policy.load(clone)
        except Refusal as error:
            broken[clone] = policy.described(error.rule, clone)
            problems.append(f"{broken[clone]}; its stories do not start")
    return local, policies, broken


def link(
    team: str,
    fleet_now: Callable[[], fleet.Fleet],
    clock: Callable[[], float],
    stores: Callable[[], Stores] | None = None,
    today: Callable[[], date] = date.today,
    kept: Kept | None = None,
) -> Linked:
    at, problems = clock(), []
    kept = kept if kept is not None else Kept()
    kept.refresh(at)
    local, policies, broken = _configured(problems)
    held = stores() if stores is not None else Stores(teams=kept.teams)
    read = fleet_now() or fleet.Fleet([], NO_FLEET)
    try:
        found = reading.read(held("app"), lambda: held("person"), team, read, local, today())
        teams = sorted({team, *(s["team"] for s in found.feature.stories)} if found.feature else {team})
        for name in teams:
            if name not in kept.labels:
                kept.labels[name] = held("app").repository_labels(name)
        labels = {name: kept.labels[name] for name in teams}
    except (LinearError, Refusal) as error:
        return Linked(None, None, at, problem(error), local, policies, tuple(problems), broken=broken)
    return Linked(held, found, at, "", local, policies, tuple(problems), labels, broken)
