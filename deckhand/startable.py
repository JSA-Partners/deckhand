from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from deckhand import asks, chores, config, features, fleet, order, planning, queue, stalls
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


def clone_of(story: dict, clones: Mapping[str, Path]) -> Path | None:
    return next((clones[label] for label in story["labels"] if label in clones), None)


def _blocked_by(story: dict, edges: list[tuple[str, str]], by_id: dict[str, dict]) -> list[str]:
    blockers = [by_id[blocker] for blocker, blocked in edges if blocked == story["id"] and blocker in by_id]
    return [s["identifier"] for s in blockers if s["state"] != order.DONE]


def kind(feature: Feature, story: dict) -> str:
    if story["state"] == order.DONE:
        return AFTER
    return DRAFT if features.phase(feature) is Phase.PLANNING or features.needs_draft(feature, story) else BUILD


def why_not_drafted(store: Store, feature: Feature, story: dict) -> str | None:
    if any(r.kind in (queue.DRAFT, *queue.TAKEN_KINDS) for r in feature.records.get(story["id"], [])):
        return f"{story['identifier']} is already drafted"
    if planning.section(store, feature, "Contract") is None:
        return f"the Contract of {features.describe(feature.project)} is not saved, so no story is drafted yet"
    open_items = features.undecided(feature, story)
    if open_items:
        noun = "item" if len(open_items) == 1 else "items"
        return f"{story['identifier']} waits on its decision {noun} {', '.join(open_items)}, not answered yet"
    return None


def why_not(
    store: Store, feature: Feature, story: dict, me: str, edges: list[tuple[str, str]], clones: Mapping[str, Path]
) -> str | None:
    ref = story["identifier"]
    if story["assignee"] not in (None, me):
        return f"{ref} belongs to {story['assignee_name']}"
    if story["state"] == order.DONE:
        if story["assignee"] != me:
            return f"{ref} is Done and not yours"
        if not chores.open_chores(story, feature.records.get(story["id"], [])):
            return f"{ref} is Done with no open after-merge item"
    elif features.phase(feature) is Phase.PLANNING or features.needs_draft(feature, story):
        drafted = why_not_drafted(store, feature, story)
        if drafted is not None:
            return drafted
    elif features.phase(feature) is not Phase.BUILDING:
        return f"{features.describe(feature.project)} is {features.phase(feature).value}, not building"
    elif feature.reapproval_due and queue.last_start(feature.records.get(story["id"], []), queue.STORY_HAND) is None:
        return f"{ref} waits for the changed plan's approval"
    else:
        waiting = _blocked_by(story, edges, {s["id"]: s for s in feature.live})
        if waiting:
            verb = "is" if len(waiting) == 1 else "are"
            return f"{ref} waits on {', '.join(waiting)}, which {verb} not Done"
    if clone_of(story, clones) is None:
        return f"no clone for {', '.join(story['labels'])} in {config.config_dir() / config.FILE}"
    return None


def startable(store: Store, feature: Feature, me: str, clones: Mapping[str, Path]) -> list[Work]:
    edges = order.edges(store, feature)
    ordered, _ = order.build_order(feature.live, edges)
    found = [s for s in ordered if why_not(store, feature, s, me, edges, clones) is None]
    build = [Work(s, kind(feature, s), clone_of(s, clones)) for s in found if s["state"] != order.DONE]
    after = [Work(s, AFTER, clone_of(s, clones)) for s in found if s["state"] == order.DONE]
    return [w for w in build + after if w.clone is not None]


def _chores(feature: Feature, me: str) -> list[dict]:
    done = [s for s in feature.live if s["state"] == order.DONE]
    return [s for s in done if s["assignee"] == me and chores.open_chores(s, feature.records.get(s["id"], []))]


# A released feature only ever starts a hand for its own after-merge items, so its build order is never read.
def after_merge(feature: Feature, me: str, clones: Mapping[str, Path]) -> list[Work]:
    return [Work(s, AFTER, clone) for s in _chores(feature, me) if (clone := clone_of(s, clones)) is not None]


def cloneless(feature: Feature, me: str, clones: Mapping[str, Path]) -> list[dict]:
    return [s for s in _chores(feature, me) if clone_of(s, clones) is None]


def launch_of(kind: str) -> str:
    return queue.DRAFT_HAND if kind == DRAFT else queue.STORY_HAND


def due(
    work: Work,
    feature: Feature,
    hands: dict[str, fleet.Session],
    policy_for: Callable[[dict], Policy],
    restarts: int | None = None,
) -> bool:
    story = work.story
    session = hands.get(story["identifier"])
    if session is not None and fleet.alive(session):
        return False
    records = feature.records.get(story["id"], [])
    launch = launch_of(work.kind)
    if queue.last_start(records, launch) is None:
        return True
    second = queue.second_due(story, records, policy_for) is not None
    if not stalls.stalled(story, records, session, launch, second):
        return False
    return stalls.auto_restart(records, launch, restarts) or stalls.stall_answer(records, launch) == stalls.RESTART


def wake_due(feature: Feature, story: dict) -> Record | None:
    records = feature.records.get(story["id"], [])
    due = asks.wake_due(records, story["id"], queue.merged_at(story))
    launch = launch_of(kind(feature, story))
    if due is None or queue.last_start(records, launch) is None:
        return None
    if launch == queue.DRAFT_HAND and any(r.kind in (queue.DRAFT, *queue.TAKEN_KINDS) for r in records):
        return None
    return due
