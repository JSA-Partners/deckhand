from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from deckhand import ops, planning, review, tiers, versions
from deckhand.records import Record, content_hash
from deckhand.step import Refusal
from deckhand.store import Store

APPROVAL = "feature-approval"
PURPOSE = "feature"
FEATURE_LABEL = "deckhand"
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


def _undecided(store: Store, feature: Feature, story: dict) -> list[str]:
    records = feature.records.get(story["id"], [])
    expected = versions.expected_items(records)
    if not isinstance(expected, list):
        return []
    found = []
    for item in expected:
        held = versions.held(records, "decision-item", versions.item_subject(str(item)))
        if held is None or not versions.current(store, feature, held):
            found.append(str(item))
    return found


def approval_gaps(store: Store, feature: Feature) -> list[str]:
    gaps = [
        f"the {name} section is not saved"
        for name in planning.SECTIONS
        if planning.section(store, feature, name) is None
    ]
    live = feature.live
    if not live:
        return [*gaps, "the feature has no stories"]
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
        level = tiers.from_record(tier.data) if tier else None
        if tier and level is None:
            gaps.append(f"{story['identifier']}: the tier record holds no level; decide the tier again")
        if level and level.level in STATE_CHART_TIERS:
            kinds.append("state-chart")
        for kind in kinds:
            version = versions.version_for(store, feature, kind, story["id"])
            gap = _gap(feature, story, kind, story["id"], version, _STORY_GAPS[kind])
            if gap:
                gaps.append(f"{story['identifier']}: {gap}")
        gaps += [
            f"{story['identifier']}: decision item {item} is not decided" for item in _undecided(store, feature, story)
        ]
    items: dict[str, str] = {}
    settled: set[str] = set()
    for story in sorted(feature.stories, key=lambda s: s["id"]):
        for record in feature.records.get(story["id"], []):
            if record.kind != "decision-item":
                continue
            items.setdefault(record.subject, str(record.data.get("item", record.subject)))
            if story in live and versions.current(store, feature, record):
                settled.add(record.subject)
    gaps += [f"decision item {items[subject]} is open" for subject in sorted(items) if subject not in settled]
    held = [record for story in live for record in feature.records.get(story["id"], [])]
    text = review_text(store, feature)
    return gaps + review.feature_gaps(project, held, content_hash(text) if text is not None else "")


def review_text(store: Store, feature: Feature) -> str | None:
    contract = planning.section(store, feature, "Contract")
    if contract is None:
        return None
    parts = ["# Contract", contract]
    for story in sorted(feature.live, key=lambda s: s["id"]):
        parts += [f"# {story['identifier']} {story['title']}", story["description"] or ""]
    return "\n\n".join(parts) + "\n"


def contract_approved(store: Store, feature: Feature) -> bool:
    project = feature.project["id"]
    version = versions.version_for(store, feature, "contract", project)
    return bool(feature.live) and all(
        _gap(feature, s, "contract", project, version, ("", "")) is None for s in feature.live
    )


def story(store: Store, feature: Feature, ref: str) -> dict:
    found = ops.issue_id_or_none(store, ref)
    held = next((s for s in feature.live if s["id"] == found), None)
    if held is None:
        raise Refusal(f"{ref} is not a story of {feature.project['name']}")
    return held


def owning(store: Store, team: str, ref: str) -> Feature:
    found = ops.issue_id_or_none(store, ref)
    issue = store.issue(found) if found else None
    held = issue is not None and issue["project"] and issue["team"] == team
    project = store.project(issue["project"]) if held else None
    if project is None or not is_feature(project):
        raise Refusal(f"{ref} is not a story of any feature of {team}")
    return load(store, project)


def crosses_repositories(store: Store, feature: Feature, story: dict) -> bool:
    group = set(store.repository_labels(story["team"]))
    edges = {end for pair in store.relations(story["id"]) for end in pair} - {story["id"]}
    mine = group & set(story["labels"])
    return any(group & set(other["labels"]) != mine for other in feature.live if other["id"] in edges)


def load(store: Store, project: dict) -> Feature:
    stories = store.project_issues(project["id"])
    feature = Feature(project, stories, {s["id"]: store.records(s["id"]) for s in stories})
    feature.version = versions.feature_version(store, feature)
    return feature


def key(team: str, name: str) -> str:
    return f"{team}:{name}"


def is_feature(project: dict) -> bool:
    return FEATURE_LABEL in project["labels"]


def team_features(store: Store, team: str) -> list[dict]:
    return [p for p in store.team_projects(team) if is_feature(p)]


def started(store: Store, team: str) -> list[Feature]:
    return [load(store, p) for p in team_features(store, team) if p["start_date"]]


def _one_open(found: list[Feature]) -> Feature | None:
    held = [f for f in found if phase(f) in (Phase.PLANNING, Phase.BUILDING)]
    if len(held) > 1:
        raise Refusal("more than one feature is open: " + ", ".join(describe(f.project) for f in held))
    return held[0] if held else None


def open_features(store: Store, team: str) -> list[Feature]:
    return [f for f in started(store, team) if phase(f) in (Phase.PLANNING, Phase.BUILDING)]


def open_feature(store: Store, team: str) -> Feature | None:
    return _one_open(started(store, team))


def current(store: Store, team: str) -> Feature | None:
    held = started(store, team)
    found = _one_open(held)
    if found is not None:
        return found
    released = [f for f in held if phase(f) is Phase.RELEASED]
    return max(released, key=_released_at, default=None)


def _released_at(feature: Feature) -> tuple[str, str, str]:
    finished = max((s.get("completed_at") or "" for s in feature.stories), default="")
    return finished, feature.project["start_date"], feature.project["id"]


def parked(store: Store, team: str) -> list[dict]:
    held = [p for p in team_features(store, team) if not p["start_date"]]
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


def budget_of(project: dict, today: date) -> Budget | None:
    if not project["start_date"] or not project["target_date"]:
        return None
    start = date.fromisoformat(project["start_date"])
    days = (date.fromisoformat(project["target_date"]) - start).days
    return Budget(days // 7, start, today) if days in (7, 14) else None
