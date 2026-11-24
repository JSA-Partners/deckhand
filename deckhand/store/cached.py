from __future__ import annotations

import copy
from typing import Any

from deckhand.records import Record
from deckhand.ref import Ref
from deckhand.store import Store

READS = frozenset(
    {
        "issue",
        "issue_by_ref",
        "repository_labels",
        "document",
        "relations",
        "project",
        "team_projects",
        "project_issues",
        "project_documents",
        "viewer",
    }
)
WRITES = frozenset(
    {
        "create_issue",
        "set_labels",
        "set_issue_text",
        "put_document",
        "relate",
        "create_project",
        "set_project_labels",
        "set_project_dates",
        "assign",
    }
)


class CachedStore(Store):
    def __init__(self, inner: Store) -> None:
        self._inner = inner
        self._memo: dict[tuple, Any] = {}

    def _read(self, method: str, *args: Any, **kwargs: Any) -> Any:
        key = (method, args, tuple(sorted(kwargs.items())))
        if key not in self._memo:
            self._memo[key] = getattr(self._inner, method)(*args, **kwargs)
        return copy.deepcopy(self._memo[key])

    def _write(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self._memo.clear()
        return getattr(self._inner, method)(*args, **kwargs)

    def create_issue(
        self,
        id: str,
        team: str,
        title: str,
        description: str,
        project: str | None,
        labels: list[str],
    ) -> str:
        return self._write("create_issue", id, team, title, description, project, labels)

    def issue(self, id: str) -> dict | None:
        return self._read("issue", id)

    def issue_by_ref(self, ref: Ref) -> dict | None:
        return self._read("issue_by_ref", ref)

    def set_labels(self, issue: str, labels: list[str]) -> None:
        self._write("set_labels", issue, labels)

    def set_issue_text(self, issue: str, title: str, description: str) -> None:
        self._write("set_issue_text", issue, title, description)

    def repository_labels(self, team: str) -> list[str]:
        return self._read("repository_labels", team)

    def put_document(self, id: str, parent: str, title: str, body: str) -> None:
        self._write("put_document", id, parent, title, body)

    def document(self, id: str) -> dict | None:
        return self._read("document", id)

    def put_record(self, issue: str, record: Record) -> None:
        self._inner.put_record(issue, record)

    def records(self, issue: str) -> list[Record]:
        return self._inner.records(issue)

    def relate(self, blocker: str, blocked: str) -> None:
        self._write("relate", blocker, blocked)

    def relations(self, issue: str) -> list[tuple[str, str]]:
        return self._read("relations", issue)

    def create_project(self, id: str, team: str, name: str, description: str, labels: list[str] | None = None) -> str:
        return self._write("create_project", id, team, name, description, labels)

    def set_project_labels(self, project: str, labels: list[str]) -> None:
        self._write("set_project_labels", project, labels)

    def project(self, id: str) -> dict | None:
        return self._read("project", id)

    def team_projects(self, team: str) -> list[dict]:
        return self._read("team_projects", team)

    def set_project_dates(self, project: str, start: str | None, target: str | None) -> None:
        self._write("set_project_dates", project, start, target)

    def project_issues(self, project: str) -> list[dict]:
        return self._read("project_issues", project)

    def project_documents(self, project: str) -> list[dict]:
        return self._read("project_documents", project)

    def assign(self, issue: str, user: str) -> None:
        self._write("assign", issue, user)

    def viewer(self) -> dict:
        return self._read("viewer")
