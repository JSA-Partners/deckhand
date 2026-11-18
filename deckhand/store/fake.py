from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from deckhand.linear.client import CONFLICT_PREFIX, DuplicateId
from deckhand.records import Record
from deckhand.ref import Ref
from deckhand.store import Store


class WriteFailed(Exception):
    pass


class FakeStore(Store):
    def __init__(self) -> None:
        self._issues: dict[str, dict[str, Any]] = {}
        self._projects: dict[str, dict[str, Any]] = {}
        self._documents: dict[str, dict[str, Any]] = {}
        self._attachments: dict[str, dict[str, dict[str, Any]]] = {}
        self._relations: list[list[str]] = []
        self._numbers: dict[str, int] = {}
        self._repositories: dict[str, list[str]] = {}
        self.writes: list[tuple] = []
        self._fail_at: int | None = None

    @classmethod
    def from_snapshot(cls, path: Path) -> FakeStore:
        data = json.loads(path.read_text())
        store = cls()
        store._issues = data["issues"]
        store._projects = data["projects"]
        store._documents = data["documents"]
        store._attachments = data["attachments"]
        store._relations = data["relations"]
        store._numbers = data["numbers"]
        return store

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "issues": self._issues,
                "projects": self._projects,
                "documents": self._documents,
                "attachments": self._attachments,
                "relations": sorted(self._relations),
                "numbers": self._numbers,
            }
        )

    def fail_after_write(self, n: int) -> None:
        self._fail_at = n

    def _wrote(self, method: str, *key: Any) -> None:
        self.writes.append((method, *key))
        if self._fail_at == len(self.writes):
            raise WriteFailed(f"write {len(self.writes)} failed after landing: {method}")

    def create_issue(
        self,
        id: str,
        team: str,
        title: str,
        description: str,
        project: str | None,
        labels: list[str],
    ) -> str:
        if id in self._issues:
            raise DuplicateId("INPUT_ERROR", f"{CONFLICT_PREFIX} Issue")
        number = self._numbers.get(team, 0) + 1
        self._numbers[team] = number
        self._issues[id] = {
            "id": id,
            "team": team,
            "number": number,
            "identifier": str(Ref(team, number)),
            "title": title,
            "description": description,
            "project": project,
            "labels": list(labels),
            "state": "Backlog",
        }
        self._wrote("create_issue", id)
        return id

    def issue(self, id: str) -> dict | None:
        found = self._issues.get(id)
        return self._public(found) if found else None

    def issue_by_ref(self, ref: Ref) -> dict | None:
        for found in self._issues.values():
            if found["team"] == ref.team and found["number"] == ref.number:
                return self._public(found)
        return None

    @staticmethod
    def _public(issue: dict[str, Any]) -> dict[str, Any]:
        return {key: copy.deepcopy(value) for key, value in issue.items() if key != "state"}

    def set_issue_state(self, issue: str, state: str) -> None:
        self._issues[issue]["state"] = state

    def set_labels(self, issue: str, labels: list[str]) -> None:
        self._issues[issue]["labels"] = list(labels)
        self._wrote("set_labels", issue, list(labels))

    def set_issue_text(self, issue: str, title: str, description: str) -> None:
        self._issues[issue]["title"] = title
        self._issues[issue]["description"] = description
        self._wrote("set_issue_text", issue, title, description)

    def add_repository_label(self, team: str, name: str) -> None:
        self._repositories.setdefault(team, []).append(name)

    def repository_labels(self, team: str) -> list[str]:
        return sorted(self._repositories.get(team, []))

    def put_document(self, id: str, parent: str, title: str, body: str) -> None:
        self._documents[id] = {"id": id, "parent": parent, "title": title, "body": body}
        self._wrote("put_document", id)

    def document(self, id: str) -> dict | None:
        found = self._documents.get(id)
        return dict(found) if found else None

    def put_record(self, issue: str, record: Record) -> None:
        url = f"https://deckhand.invalid/record/{record.kind}/{record.subject}"
        attachment = {"url": url, "metadata": {"record": record.to_json()}}
        self._attachments.setdefault(issue, {})[url] = attachment
        self._wrote("put_record", issue, record.kind, record.subject)

    def records(self, issue: str) -> list[Record]:
        held = self._attachments.get(issue, {})
        return [Record.from_json(held[url]["metadata"]["record"]) for url in sorted(held)]

    def relate(self, blocker: str, blocked: str) -> None:
        self._relations.append([blocker, blocked])
        self._wrote("relate", blocker, blocked)

    def relations(self, issue: str) -> list[tuple[str, str]]:
        return [(a, b) for a, b in sorted(self._relations) if issue in (a, b)]

    def create_project(self, id: str, team: str, name: str, description: str) -> str:
        if id in self._projects:
            raise DuplicateId("INPUT_ERROR", f"{CONFLICT_PREFIX} Project")
        self._projects[id] = {
            "id": id,
            "team": team,
            "name": name,
            "description": description,
            "start_date": None,
            "target_date": None,
        }
        self._wrote("create_project", id)
        return id

    @staticmethod
    def _project(project: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in project.items() if key != "team"}

    def project(self, id: str) -> dict | None:
        found = self._projects.get(id)
        return self._project(found) if found else None

    def team_projects(self, team: str) -> list[dict]:
        held = [p for p in self._projects.values() if p["team"] == team]
        return [self._project(p) for p in sorted(held, key=lambda p: p["id"])]

    def set_project_dates(self, project: str, start: str | None, target: str | None) -> None:
        self._projects[project]["start_date"] = start
        self._projects[project]["target_date"] = target
        self._wrote("set_project_dates", project, start, target)

    def project_issues(self, project: str) -> list[dict]:
        held = [i for i in self._issues.values() if i["project"] == project]
        held.sort(key=lambda i: (i["team"], i["number"]))
        return [copy.deepcopy(i) for i in held]
