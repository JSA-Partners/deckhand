from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from deckhand import asks, config, features, fleet, order, queue, signoff
from deckhand.features import Feature, Phase
from deckhand.policy import Policy
from deckhand.records import Record
from deckhand.store import Store

BUILD, AFTER, DRAFT = "build", "after-merge", "draft"


@dataclass(frozen=True)
class Work:
    story: dict
    kind: str
    clone: Path


def clone_of(story: dict) -> Path | None:
    clones = config.local().clones
    return next((clones[label] for label in story["labels"] if label in clones), None)


def _open_chores(story: dict, records: list[Record]) -> bool:
    chores = enumerate(signoff.after_items(records, story["id"]), start=1)
    return any(signoff.after_status(records, story["id"], n, text)[0] == signoff.OPEN for n, text in chores)


def _blocked_by(story: dict, edges: list[tuple[str, str]], by_id: dict[str, dict]) -> list[str]:
    blockers = [by_id[blocker] for blocker, blocked in edges if blocked == story["id"] and blocker in by_id]
    return [s["identifier"] for s in blockers if s["state"] != order.DONE]


def kind(feature: Feature, story: dict) -> str:
    if story["state"] == order.DONE:
        return AFTER
    return DRAFT if features.phase(feature) is Phase.PLANNING else BUILD


def why_not_drafted(store: Store, feature: Feature, story: dict) -> str | None:
    if any(r.kind in (queue.DRAFT, queue.TAKEN) for r in feature.records.get(story["id"], [])):
        return f"{story['identifier']} is already drafted"
    if not features.contract_approved(store, feature):
        return f"the Contract of {features.describe(feature.project)} is not approved, so no story is drafted yet"
    return None


def why_not(store: Store, feature: Feature, story: dict, me: str, edges: list[tuple[str, str]]) -> str | None:
    ref = story["identifier"]
    if story["assignee"] not in (None, me):
        return f"{ref} belongs to {story['assignee_name']}"
    if story["state"] == order.DONE:
        if story["assignee"] != me:
            return f"{ref} is Done and not yours"
        if not _open_chores(story, feature.records.get(story["id"], [])):
            return f"{ref} is Done with no open after-merge item"
    elif features.phase(feature) is Phase.PLANNING:
        drafted = why_not_drafted(store, feature, story)
        if drafted is not None:
            return drafted
    elif features.phase(feature) is not Phase.BUILDING:
        return f"{features.describe(feature.project)} is {features.phase(feature).value}, not building"
    else:
        waiting = _blocked_by(story, edges, {s["id"]: s for s in feature.live})
        if waiting:
            verb = "is" if len(waiting) == 1 else "are"
            return f"{ref} waits on {', '.join(waiting)}, which {verb} not Done"
    if clone_of(story) is None:
        return f"no clone for {', '.join(story['labels'])} in {config.config_dir() / config.FILE}"
    return None


def startable(store: Store, feature: Feature, me: str) -> list[Work]:
    edges = order.edges(store, feature)
    ordered, _ = order.build_order(feature.live, edges)
    found = [s for s in ordered if why_not(store, feature, s, me, edges) is None]
    build = [Work(s, kind(feature, s), clone_of(s)) for s in found if s["state"] != order.DONE]
    after = [Work(s, AFTER, clone_of(s)) for s in found if s["state"] == order.DONE]
    return [w for w in build + after if w.clone is not None]


def launch_of(kind: str) -> str:
    return queue.DRAFT_HAND if kind == DRAFT else queue.STORY_HAND


def due(work: Work, feature: Feature, hands: dict[str, fleet.Session]) -> bool:
    story = work.story
    session = hands.get(story["identifier"])
    if session is not None and fleet.live(session):
        return False
    records = feature.records.get(story["id"], [])
    launch = launch_of(work.kind)
    if queue.last_start(records, launch) is None:
        return True
    policy = Policy.load(work.clone)
    second = queue.second_due(story, records, lambda _: policy) is not None
    return queue.restart_due(story, records, session, launch, second)


def wake_due(feature: Feature, story: dict) -> Record | None:
    records = feature.records.get(story["id"], [])
    due = asks.wake_due(records, story["id"])
    launch = launch_of(kind(feature, story))
    if due is None or queue.last_start(records, launch) is None:
        return None
    # A decision made before the merge was for the build the merge finished.
    if story["state"] == order.DONE and due.at <= (story.get("completed_at") or ""):
        return None
    if launch == queue.DRAFT_HAND and any(r.kind in (queue.DRAFT, queue.TAKEN) for r in records):
        return None
    return due
