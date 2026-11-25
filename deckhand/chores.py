from __future__ import annotations

from deckhand import features, implplan, order, review, signoff, versions
from deckhand.records import HAND, Record, content_hash
from deckhand.store import Store

AFTER = "after-merge"
AFTER_DONE = "after-merge-done"
AFTER_PERSON = "after-merge-person"
OPEN, BY_HAND, FOR_PERSON, BY_PERSON = "open", "done by the hand", "left for the person", "done by the person"


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
    if (
        decided is not None
        and decided.author.startswith(review.PERSON)
        and decided.version == left.version
        and decided.data.get("answer") == signoff.DONE
    ):
        return BY_PERSON, decided
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


def _left_elsewhere(store: Store, team: str, seen: set[str]) -> list[dict]:
    projects: set[str] = set()
    for issue in store.recorded(AFTER_PERSON):
        if issue in seen or not _left(store.records(issue), issue):
            continue
        held = store.issue(issue)
        if held is not None and held["project"] and held["team"] == team:
            projects.add(held["project"])
    found = [store.project(id) for id in sorted(projects)]
    return [p for p in found if p is not None and features.is_feature(p)]


def after_merge_queue(store: Store, team: str, me: str) -> list[tuple[dict, int, str, str, str]]:
    held = features.open_features(store, team)
    for project in _left_elsewhere(store, team, {s["id"] for feature in held for s in feature.stories}):
        held.append(features.load(store, project))
    found = []
    for feature in held:
        for story in feature.stories:
            if story["assignee"] == me:
                held = _left(feature.records.get(story["id"], []), story["id"])
                found += [(story, n, text, str(left.data.get("why", "")), left.at) for n, text, left in held]
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
