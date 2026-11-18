from __future__ import annotations

import hashlib
import uuid
from typing import Any

from deckhand.linear.client import DuplicateId
from deckhand.records import Record
from deckhand.ref import Ref
from deckhand.step import Refusal
from deckhand.store import Store


def create_id(key: str, purpose: str) -> str:
    digest = bytearray(hashlib.sha256(f"deckhand\0{key}\0{purpose}".encode()).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def ensure_issue(store: Store, key: str, purpose: str, **fields: Any) -> str:
    id = create_id(key, purpose)
    if store.issue(id) is not None:
        return id
    try:
        store.create_issue(id, **fields)
    except DuplicateId:
        pass
    return id


def ensure_labels(store: Store, issue: str, labels: list[str]) -> None:
    found = store.issue(issue)
    if found is not None and set(found["labels"]) == set(labels):
        return
    store.set_labels(issue, sorted(labels))


def ensure_issue_text(store: Store, issue: str, title: str, description: str) -> None:
    found = store.issue(issue)
    if found is not None and (found["title"], found["description"]) == (title, description):
        return
    store.set_issue_text(issue, title, description)


def ensure_document(store: Store, key: str, purpose: str, parent: str, title: str, body: str) -> str:
    id = create_id(key, purpose)
    found = store.document(id)
    if found is None or found["title"] != title or found["body"] != body:
        store.put_document(id, parent, title, body)
    return id


def ensure_record(store: Store, issue: str, record: Record) -> None:
    if any(held.to_json() == record.to_json() for held in store.records(issue)):
        return
    store.put_record(issue, record)


def _issue_id(store: Store, issue: str) -> str:
    try:
        ref = Ref.parse(issue)
    except ValueError:
        return issue
    found = store.issue_by_ref(ref)
    if found is None:
        raise Refusal(f"no issue {issue}")
    return found["id"]


def ensure_relation(store: Store, blocker: str, blocked: str) -> None:
    blocker, blocked = _issue_id(store, blocker), _issue_id(store, blocked)
    if (blocker, blocked) not in store.relations(blocker):
        store.relate(blocker, blocked)


def ensure_project(store: Store, key: str, purpose: str, team: str, name: str, description: str) -> str:
    id = create_id(key, purpose)
    if store.project(id) is not None:
        return id
    try:
        store.create_project(id, team, name, description)
    except DuplicateId:
        pass
    return id


def ensure_project_dates(store: Store, project: str, start: str | None, target: str | None) -> None:
    found = store.project(project)
    if found is not None and (found["start_date"], found["target_date"]) == (start, target):
        return
    store.set_project_dates(project, start, target)
