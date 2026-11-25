from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from deckhand import ops, planning, review_gaps, versions
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

    @property
    def approved_once(self) -> bool:
        return any(self.approved(s) for s in self.live)

    @property
    def reapproval_due(self) -> bool:
        return self.approved_once and not all(self.approval_current(s) for s in self.live)


def phase(feature: Feature) -> Phase:
    if not feature.project["start_date"]:
        return Phase.PARKED
    stories = feature.stories
    if stories and all(s["state"] in FINISHED for s in stories):
        return Phase.RELEASED
    return Phase.BUILDING if feature.approved_once else Phase.PLANNING


def replanning(feature: Feature) -> bool:
    current = phase(feature)
    return current is Phase.PLANNING or (current is Phase.BUILDING and feature.reapproval_due)


def undecided(feature: Feature, story: dict) -> list[str]:
    records = feature.records.get(story["id"], [])
    found = []
    for item, draft in versions.drafts(records).items():
        held = versions.held(records, "decision-item", versions.item_subject(item))
        if held is None or not versions.settles(held, item, draft):
            found.append(item)
    return found


def needs_draft(feature: Feature, story: dict) -> bool:
    from deckhand import asks, queue

    records = feature.records.get(story["id"], [])
    built = any(r.kind == asks.HAND_START and r.data.get("kind") == queue.STORY_HAND for r in records)
    drafted = any(r.kind in (queue.DRAFT, *queue.TAKEN_KINDS) for r in records)
    return feature.reapproval_due and story["state"] not in FINISHED and not built and not drafted


def _draft_gaps(records: list[Record], ref: str, due: bool = False) -> list[str]:
    from deckhand import queue, stalls

    kinds = {r.kind for r in records}
    if kinds & set(queue.TAKEN_KINDS):
        return []
    if queue.DRAFT in kinds:
        return [f"{ref}: its draft is not taken"]
    if stalls.stall_answer(records, queue.DRAFT_HAND) == stalls.LEAVE:
        return []
    if queue.last_start(records, queue.DRAFT_HAND):
        return [f"{ref}: its draft is being written"]
    return [f"{ref}: it has no draft yet"] if due else []


def _map_gaps(feature: Feature, story: dict) -> list[str]:
    records, ref = feature.records.get(story["id"], []), story["identifier"]
    level = versions.tier(records, story["id"])
    if level is None:
        found = [f"{ref}: the story map gives no tier; save the map again"]
    elif level.level in STATE_CHART_TIERS and not versions.chart(records, story["id"]).strip():
        found = [f"{ref}: tier {level.level} needs a state chart in the story map"]
    else:
        found = []
    return found + _draft_gaps(records, ref, needs_draft(feature, story))


def approval_gaps(store: Store, feature: Feature, captain: bool = False) -> list[str]:
    review = [gap.hand if captain else gap.person for gap in review_gap_list(store, feature)]
    return [*plain_gaps(store, feature), *review]


def plain_gaps(store: Store, feature: Feature) -> list[str]:
    gaps = [
        f"the {name} section is not saved"
        for name in planning.SECTIONS
        if planning.section(store, feature, name) is None
    ]
    live = feature.live
    if not live:
        return [*gaps, "the feature has no stories"]
    for story in live:
        gaps += _map_gaps(feature, story)
        gaps += [f"{story['identifier']}: decision item {item} is not decided" for item in undecided(feature, story)]
    items: dict[str, str] = {}
    settled: set[str] = set()
    for story in sorted(feature.stories, key=lambda s: s["id"]):
        records = feature.records.get(story["id"], [])
        drafts = {versions.item_subject(i): (i, d) for i, d in versions.drafts(records).items()}
        for record in records:
            if record.kind != "decision-item":
                continue
            items.setdefault(record.subject, str(record.data.get("item", record.subject)))
            drafted = drafts.get(record.subject)
            if story in live and (drafted is None or versions.settles(record, *drafted)):
                settled.add(record.subject)
    return gaps + [f"decision item {items[subject]} is open" for subject in sorted(items) if subject not in settled]


def review_gap_list(store: Store, feature: Feature) -> list[review_gaps.Gap]:
    if not feature.live:
        return []
    project = feature.project["id"]
    held = [record for story in feature.live for record in feature.records.get(story["id"], [])]
    text = review_text(store, feature)
    return review_gaps.feature_gaps(project, held, content_hash(text) if text is not None else "", project)


def review_text(store: Store, feature: Feature) -> str | None:
    contract = planning.section(store, feature, "Contract")
    if contract is None:
        return None
    parts = ["# Contract", contract]
    for story in sorted(feature.live, key=lambda s: s["id"]):
        parts += [f"# {story['identifier']} {story['title']}", story["description"] or ""]
    return "\n\n".join(parts) + "\n"


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
    if phase(feature) is not Phase.RELEASED:
        feature.version = versions.feature_version(store, feature)
    return feature


def key(team: str, name: str) -> str:
    return f"{team}:{name}"


def is_feature(project: dict) -> bool:
    return FEATURE_LABEL in project["labels"]


def team_features(store: Store, team: str) -> list[dict]:
    return [p for p in store.team_projects(team) if is_feature(p)]


def _started(store: Store, team: str, finished: bool) -> list[Feature]:
    held = [p for p in team_features(store, team) if p["start_date"] and p["finished"] is finished]
    return [load(store, p) for p in held]


def _one_open(found: list[Feature]) -> Feature | None:
    held = [f for f in found if phase(f) in (Phase.PLANNING, Phase.BUILDING)]
    if len(held) > 1:
        raise Refusal("more than one feature is open: " + ", ".join(describe(f.project) for f in held))
    return held[0] if held else None


def open_features(store: Store, team: str) -> list[Feature]:
    return [f for f in _started(store, team, finished=False) if phase(f) in (Phase.PLANNING, Phase.BUILDING)]


def open_feature(store: Store, team: str) -> Feature | None:
    return _one_open(open_features(store, team))


def current(store: Store, team: str) -> Feature | None:
    found = open_feature(store, team)
    if found is not None:
        return found
    held = [p for p in team_features(store, team) if p["start_date"] and p["finished"]]
    latest = max((_dates(p) for p in held), default=None)
    # Features released on the same dates are told apart by their last merge, so only those are loaded.
    tied = [load(store, p) for p in held if _dates(p) == latest]
    return max(tied, key=_last_merge, default=None)


def _dates(project: dict) -> tuple[str, str]:
    return project["target_date"] or "", project["start_date"]


def _last_merge(feature: Feature) -> tuple[str, str]:
    return max((s.get("completed_at") or "" for s in feature.stories), default=""), feature.project["id"]


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
