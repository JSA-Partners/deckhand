from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.store import Store

APPROVAL = "feature-approval"
FINISHED = ("Done", "Canceled", "Duplicate")


class Phase(Enum):
    PARKED = "parked"
    PLANNING = "planning"
    BUILDING = "building"
    RELEASED = "released"


@dataclass
class Feature:
    project: dict
    stories: list[dict] = field(default_factory=list)
    records: dict[str, list[Record]] = field(default_factory=dict)

    def approved(self, story: dict) -> bool:
        return any(r.kind == APPROVAL for r in self.records.get(story["id"], []))


def phase(feature: Feature) -> Phase:
    if not feature.project["start_date"]:
        return Phase.PARKED
    stories = feature.stories
    if stories and all(s["state"] in FINISHED for s in stories):
        return Phase.RELEASED
    live = [s for s in stories if s["state"] not in ("Canceled", "Duplicate")]
    if live and all(feature.approved(s) for s in live):
        return Phase.BUILDING
    return Phase.PLANNING


def load(store: Store, project: dict) -> Feature:
    stories = store.project_issues(project["id"])
    return Feature(project, stories, {s["id"]: store.records(s["id"]) for s in stories})


def open_features(store: Store, team: str) -> list[Feature]:
    found = [load(store, p) for p in store.team_projects(team) if p["start_date"]]
    return [f for f in found if phase(f) in (Phase.PLANNING, Phase.BUILDING)]


def open_feature(store: Store, team: str) -> Feature | None:
    found = open_features(store, team)
    if len(found) > 1:
        raise Refusal("more than one feature is open: " + ", ".join(describe(f.project) for f in found))
    return found[0] if found else None


def parked(store: Store, team: str) -> list[dict]:
    held = [p for p in store.team_projects(team) if not p["start_date"]]
    return sorted(held, key=lambda p: (p["name"], p["id"]))


def describe(project: dict) -> str:
    return f"{project['name']} ({project['id']})"


@dataclass(frozen=True)
class Budget:
    weeks: int
    start: date
    today: date

    @property
    def days_used(self) -> int:
        return (self.today - self.start).days

    @property
    def budget_days(self) -> int:
        return 7 * self.weeks

    @property
    def planning_note(self) -> bool:
        return self.days_used > 3.5

    @property
    def over(self) -> bool:
        return self.days_used > self.budget_days

    def line(self) -> str:
        return f"{self.days_used} of {self.budget_days} days used" + (" (over budget)" if self.over else "")


def budget_of(project: dict, today: date) -> Budget | None:
    if not project["start_date"] or not project["target_date"]:
        return None
    start = date.fromisoformat(project["start_date"])
    days = (date.fromisoformat(project["target_date"]) - start).days
    return Budget(days // 7, start, today) if days in (7, 14) else None
