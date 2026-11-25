from __future__ import annotations

from dataclasses import replace

from deckhand import asks, features, implplan, ops, order, review, signoff, versions
from deckhand.records import HAND, Record, content_hash
from deckhand.store import Store

AFTER = "after-merge"
AFTER_DONE = "after-merge-done"
AFTER_PERSON = "after-merge-person"
OPEN, BY_HAND, FOR_PERSON, BY_PERSON = "open", "done by the hand", "left for the person", "done by the person"
DROPPED, STORY = "dropped", "made into a story"
DROP, MAKE_STORY = "Drop", "Make it a story"
ENGINE = "agent:engine"
STOPPED_WHY = "its hand stopped after the merge"
NO_CLONE_WHY = "no clone of its repository on this machine"
NAME_CAP = 64
_DECIDED = {signoff.DONE: BY_PERSON, DROP: DROPPED, MAKE_STORY: STORY}


def after_items(records: list[Record], story: str) -> list[str]:
    plan = versions.held(records, implplan.PURPOSE, story)
    return [str(text) for text in plan.data.get("after_merge", [])] if plan else []


def after_record(kind: str, story: str, n: int, text: str, data: dict) -> Record:
    return Record(kind, f"{story}:{n}", content_hash(text), HAND, {"n": n, "text": text} | data)


def after_status(records: list[Record], story: str, n: int, text: str) -> tuple[str, Record | None]:
    subject, at = f"{story}:{n}", content_hash(text)
    done = versions.held(records, AFTER_DONE, subject)
    if done is not None and done.version == at:
        return BY_HAND, done
    left = versions.held(records, AFTER_PERSON, subject)
    if left is None or left.version != at:
        return OPEN, None
    decided = versions.held(records, AFTER, subject)
    if decided is not None and decided.author.startswith(review.PERSON) and decided.version == left.version:
        status = _DECIDED.get(str(decided.data.get("answer")))
        if status is not None:
            return status, decided
    return FOR_PERSON, left


def _left(records: list[Record], story: str) -> list[tuple[int, str, Record]]:
    found = []
    for n, text in enumerate(after_items(records, story), start=1):
        status, held = after_status(records, story, n, text)
        if status == FOR_PERSON and held is not None:
            found.append((n, text, held))
    return found


def after_merge_for_person(records: list[Record], story: str) -> list[tuple[int, str, str]]:
    return [(n, text, str(held.data.get("why", ""))) for n, text, held in _left(records, story)]


def settled(status: str) -> bool:
    return status in (BY_HAND, BY_PERSON, DROPPED, STORY)


def _unsettled(records: list[Record], story: str) -> int:
    items = enumerate(after_items(records, story), start=1)
    return sum(not settled(after_status(records, story, n, text)[0]) for n, text in items)


def unsettled(feature: features.Feature) -> int:
    return sum(_unsettled(feature.records.get(s["id"], []), s["id"]) for s in feature.live)


def open_in(feature: features.Feature) -> bool:
    return unsettled(feature) > 0


def _wanted(records: list[Record], story: str, alive: frozenset[str]) -> bool:
    start = max((r for r in records if r.kind == asks.HAND_START), key=lambda r: r.at, default=None)
    return _unsettled(records, story) > 0 or (start is not None and start.data.get("session") in alive)


# A feature whose hand is still alive stays loaded, so the engine can stop that hand once its items are done.
def elsewhere(store: Store, team: str, seen: set[str], alive: frozenset[str] = frozenset()) -> list[features.Feature]:
    signed = set(store.recorded(signoff.KIND)) & set(store.recorded(implplan.PURPOSE))
    candidates = sorted(id for id in signed - seen if _wanted(store.records(id), id, alive))
    if not candidates:
        return []
    live = [i for i in store.issues(candidates) if i["team"] == team and i["state"] not in features.DROPPED]
    owning = {issue["project"] for issue in live}
    held = sorted((p for p in features.team_features(store, team) if p["id"] in owning), key=lambda p: p["id"])
    return [features.load(store, p) for p in held]


def after_merge_queue(held: list[features.Feature], me: str) -> list[tuple[dict, int, str, str, str]]:
    found = []
    for feature in held:
        for story in feature.stories:
            if story["assignee"] == me:
                left = _left(feature.records.get(story["id"], []), story["id"])
                found += [(story, n, text, str(record.data.get("why", "")), record.at) for n, text, record in left]
    return found


def finished(story: dict, records: list[Record], start: Record) -> bool:
    from deckhand import queue

    if start.data.get("kind") == queue.DRAFT_HAND and any(r.kind == queue.DRAFT and r.at >= start.at for r in records):
        return True
    if story["state"] != order.DONE:
        return False
    chores = after_items(records, story["id"])
    statuses = [after_status(records, story["id"], n, text)[0] for n, text in enumerate(chores, start=1)]
    return OPEN not in statuses


def open_chores(story: dict, records: list[Record]) -> bool:
    items = enumerate(after_items(records, story["id"]), start=1)
    return any(after_status(records, story["id"], n, text)[0] == OPEN for n, text in items)


def hand_over(story: dict, records: list[Record], session: str, why: str = STOPPED_WHY) -> tuple[Record, ...]:
    found = []
    for n, text in enumerate(after_items(records, story["id"]), start=1):
        if after_status(records, story["id"], n, text)[0] == OPEN:
            left = after_record(AFTER_PERSON, story["id"], n, text, {"why": why, "session": session})
            found.append(replace(left, author=ENGINE))
    return tuple(found)


def story_name(text: str) -> str:
    name = f"Chore: {text.strip().splitlines()[0] if text.strip() else ''}"
    if len(name) <= NAME_CAP:
        return name
    room = NAME_CAP - len("...")
    head, space, _ = name[: room + 1].rpartition(" ")
    return (head if space and len(head) > len("Chore:") else name[:room]) + "..."


def make_story(store: Store, team: str, story: dict, n: int, text: str, why: str) -> str:
    ref = f"{story['identifier']} {story['title']}"
    description = f"{text}\n\nAfter-merge item {n} of {ref}, left to the person: {why}"
    key = f"{team}:chore:{story['id']}:{n}"
    return ops.ensure_project(
        store, key, features.PURPOSE, team, story_name(text), description, [features.FEATURE_LABEL]
    )
