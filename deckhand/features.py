from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from deckhand import ops, tiers, versions
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.store import Store

APPROVAL = "feature-approval"
FINISHED = ("Done", "Canceled", "Duplicate")
DROPPED = ("Canceled", "Duplicate")
STATE_CHART_TIERS = (2, 3)


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
    version: str | None = None

    @property
    def live(self) -> list[dict]:
        return [s for s in self.stories if s["state"] not in DROPPED]

    def approved(self, story: dict) -> bool:
        return any(r.kind == APPROVAL for r in self.records.get(story["id"], []))

    def approval_current(self, story: dict) -> bool:
        return any(r.kind == APPROVAL and r.version == self.version for r in self.records.get(story["id"], []))


def phase(feature: Feature) -> Phase:
    if not feature.project["start_date"]:
        return Phase.PARKED
    stories = feature.stories
    if stories and all(s["state"] in FINISHED for s in stories):
        return Phase.RELEASED
    live = feature.live
    if live and all(feature.approval_current(s) for s in live):
        return Phase.BUILDING
    return Phase.PLANNING


_SECTION_GAPS = {
    "contract": ("the Contract is not approved", "the Contract changed after it was approved"),
    "invariants": ("the Invariants are not approved", "the Invariants changed after they were approved"),
}
_STORY_GAPS = {
    "tier": ("the tier is not decided", "the tier changed after it was decided"),
    "sitting": ("the sitting is not done", "the sitting changed after it was done"),
    "state-chart": ("the state chart is not approved", "the state chart changed after it was approved"),
}


def _gap(feature: Feature, story: dict, kind: str, subject: str, version: str, words: tuple[str, str]) -> str | None:
    record = versions.held(feature.records.get(story["id"], []), kind, subject)
    if record is None:
        return words[0]
    return None if record.version == version else words[1]


def approval_gaps(store: Store, feature: Feature) -> list[str]:
    live = feature.live
    if not live:
        return ["the feature has no stories"]
    gaps = []
    project = feature.project["id"]
    for kind, words in _SECTION_GAPS.items():
        version = versions.version_for(store, feature, kind, project)
        found = {_gap(feature, s, kind, project, version, words) for s in live}
        if words[1] in found:
            gaps.append(words[1])
        elif words[0] in found:
            gaps.append(words[0])
    for story in live:
        kinds = ["tier", "sitting"]
        tier = versions.held(feature.records.get(story["id"], []), "tier", story["id"])
        if tier and tiers.tier(tiers.chosen(str(tier.data.get("answer", "")))).level in STATE_CHART_TIERS:
            kinds.append("state-chart")
        for kind in kinds:
            version = versions.version_for(store, feature, kind, story["id"])
            gap = _gap(feature, story, kind, story["id"], version, _STORY_GAPS[kind])
            if gap:
                gaps.append(f"{story['identifier']}: {gap}")
    items: dict[str, Record] = {}
    for story in live:
        for record in feature.records.get(story["id"], []):
            if record.kind == "decision-item":
                items[record.subject] = record
    for subject in sorted(items):
        answer = items[subject].data.get("answer")
        if answer != "Keep":
            gaps.append(f"decision item {items[subject].data.get('item', subject)} is open ({answer})")
    return gaps


def story(store: Store, feature: Feature, ref: str) -> dict:
    found = ops.issue_id_or_none(store, ref)
    held = next((s for s in feature.live if s["id"] == found), None)
    if held is None:
        raise Refusal(f"{ref} is not a story of {feature.project['name']}")
    return held


def load(store: Store, project: dict) -> Feature:
    stories = store.project_issues(project["id"])
    feature = Feature(project, stories, {s["id"]: store.records(s["id"]) for s in stories})
    feature.version = versions.feature_version(store, feature)
    return feature


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
