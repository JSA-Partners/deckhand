from __future__ import annotations

import inspect
from typing import Any

from deckhand.records import Record
from deckhand.store import Store
from deckhand.store.cached import READS, WRITES, CachedStore
from deckhand.store.fake import FakeStore

TEAM = "team-1"
PROJECT = "proj-1"
ISSUE = "7c1f3a52-0b7e-4c1e-9a55-3f7a1d2b9e10"


class Counting(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def issue(self, id: str) -> dict | None:
        self.calls.append("issue")
        return super().issue(id)

    def project_issues(self, project: str) -> list[dict]:
        self.calls.append("project_issues")
        return super().project_issues(project)


def seeded() -> tuple[Counting, CachedStore]:
    inner = Counting()
    inner.create_project(PROJECT, TEAM, "Feature", "", [])
    inner.create_issue(ISSUE, TEAM, "Title", "Body", PROJECT, [])
    return inner, CachedStore(inner)


def test_every_store_method_is_a_read_a_write_or_a_record_call() -> None:
    names = {n for n, _ in inspect.getmembers(Store, inspect.isfunction) if not n.startswith("_")}
    assert names == READS | WRITES | {"put_record", "records"}
    assert not READS & WRITES


def test_every_store_method_is_defined_on_the_cache_itself() -> None:
    assert READS | WRITES | {"put_record", "records"} <= set(vars(CachedStore))


def test_a_repeated_read_reaches_the_inner_store_once() -> None:
    inner, cached = seeded()
    first = cached.project_issues(PROJECT)
    assert cached.project_issues(PROJECT) == first
    assert inner.calls == ["project_issues"]


def test_a_write_clears_every_cached_read() -> None:
    inner, cached = seeded()
    cached.issue(ISSUE)
    cached.set_issue_text(ISSUE, "New", "Body")
    found = cached.issue(ISSUE)
    assert inner.calls == ["issue", "issue"]
    assert found is not None and found["title"] == "New"


def test_records_are_never_cached() -> None:
    _, cached = seeded()
    record = Record("tier", "s", "v1", "person", {"level": 1})
    assert cached.records(ISSUE) == []
    cached.put_record(ISSUE, record)
    assert [r.subject for r in cached.records(ISSUE)] == ["s"]


def test_a_cached_result_cannot_be_changed_by_the_caller() -> None:
    _, cached = seeded()
    got: Any = cached.issue(ISSUE)
    got["title"] = "Changed"
    again: Any = cached.issue(ISSUE)
    assert again["title"] == "Title"
