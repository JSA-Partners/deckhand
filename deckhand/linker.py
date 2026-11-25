from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from deckhand import budget, config, fleet, mirror, planning, policy, reading
from deckhand.linear.client import LinearError, default_transport
from deckhand.policy import Policy
from deckhand.reading import Reading
from deckhand.step import Refusal
from deckhand.store import open as store_open
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
    contract: str = ""


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
    for label, clone in local.clones.items():
        try:
            policies[clone] = Policy.load(clone)
        except Refusal as error:
            broken[clone] = policy.described(error.rule, clone, label)
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
        # Reads the Contract here, so the engine's mirrors never make a Linear read of their own.
        held_contract = None
        if found.feature is not None and mirror.needs_contract(found.feature):
            held_contract = planning.document(held("app"), found.feature, "Contract")
    except (LinearError, Refusal) as error:
        return Linked(None, None, at, problem(error), local, policies, tuple(problems), broken=broken)
    contract = held_contract["url"] if held_contract else ""
    return Linked(held, found, at, "", local, policies, tuple(problems), labels, broken, contract)


# The poller calls this on its timer, on a poke and on a wake alike, so the budget gates all three.
class Linker:
    def __init__(self, team: str, fleet_now: Callable[[], fleet.Fleet], clock: Callable[[], float]) -> None:
        self._team, self._fleet_now, self._clock = team, fleet_now, clock
        self._kept = Kept()
        self._last: Linked | None = None

    def __call__(self) -> Linked:
        last = self._last
        if last is not None and not budget.due(budget.current(), self._clock(), last.at):
            return last
        default_transport.revive()
        try:
            self._last = link(self._team, self._fleet_now, self._clock, kept=self._kept)
        finally:
            store_open.flush()
        return self._last
