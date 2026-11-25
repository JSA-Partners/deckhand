from __future__ import annotations

import re
from typing import Any

from deckhand.linear.client import CONFLICT_PREFIX, DuplicateId, LinearError
from deckhand.linear.workspace import GROUP
from deckhand.ref import Ref
from deckhand.store.fake import FakeStore

DONE = {"hasNextPage": False, "endCursor": None}


def team_id(key: str) -> str:
    return f"team-{key}"


def _page(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"nodes": nodes, "pageInfo": DONE}


def _labels(names: list[str]) -> dict[str, Any]:
    return _page([{"id": name, "name": name} for name in names])


def _missing(entity: str) -> LinearError:
    return LinearError("INPUT_ERROR", f"Entity not found: {entity}")


class FakeGraphQL:
    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    @property
    def queries(self) -> list[str]:
        return [name for name in self.names if name not in MUTATIONS]

    def query(self, document: str, variables: dict[str, Any]) -> dict[str, Any]:
        match = re.search(r"(?:query|mutation) (\w+)", document)
        assert match is not None, document
        name = match.group(1)
        self.calls.append((name, variables))
        return getattr(self, f"_{name}")(variables)

    def _issue(self, id: str) -> dict[str, Any]:
        try:
            found = self.store.issue_by_ref(Ref.parse(id))
        except ValueError:
            found = self.store.issue(id)
        if found is None:
            raise _missing("Issue")
        return found

    def _issue_node(self, held: dict[str, Any]) -> dict[str, Any]:
        owner = held["assignee"]
        return {
            "id": held["id"],
            "identifier": held["identifier"],
            "number": held["number"],
            "branchName": held["branch_name"],
            "title": held["title"],
            "description": held["description"],
            "team": {"key": held["team"]},
            "project": {"id": held["project"]} if held["project"] else None,
            "assignee": {"id": owner, "name": held["assignee_name"]} if owner else None,
            "labels": _labels(held["labels"]),
        }

    def _relations(self, id: str) -> dict[str, Any]:
        pairs = self.store.relations(id)
        return {
            "relations": _page([{"type": "blocks", "relatedIssue": {"id": b}} for a, b in pairs if a == id]),
            "inverseRelations": _page([{"type": "blocks", "issue": {"id": a}} for a, b in pairs if b == id]),
        }

    def _project_node(self, project: dict[str, Any]) -> dict[str, Any]:
        stories = self.store.project_issues(project["id"])
        return {
            "id": project["id"],
            "name": project["name"],
            "description": project["description"],
            "startDate": project["start_date"],
            "targetDate": project["target_date"],
            "labels": _page([{"name": name} for name in project["labels"]]),
            "anyIssue": _page([{"id": s["id"]} for s in stories[:1]]),
            "unfinished": _page([] if project["finished"] else [{"id": s["id"]} for s in stories[:1]]),
        }

    def _key(self, id: str) -> str:
        return id.removeprefix("team-")

    def _TeamId(self, variables: dict[str, Any]) -> dict[str, Any]:
        return {"teams": {"nodes": [{"id": team_id(variables["key"])}]}}

    def _LabelIds(self, variables: dict[str, Any]) -> dict[str, Any]:
        return {"issueLabels": {"nodes": [{"id": name, "name": name} for name in variables["names"]]}}

    def _ProjectLabelIds(self, variables: dict[str, Any]) -> dict[str, Any]:
        return {"projectLabels": {"nodes": [{"id": name, "name": name} for name in variables["names"]]}}

    def _Issue(self, variables: dict[str, Any]) -> dict[str, Any]:
        return {"issue": self._issue_node(self._issue(variables["id"]))}

    def _Issues(self, variables: dict[str, Any]) -> dict[str, Any]:
        nodes = []
        for id in variables["ids"]:
            found = self.store.issue(id)
            if found is None:
                continue
            siblings = self.store.project_issues(found["project"]) if found["project"] else []
            held = next((i for i in siblings if i["id"] == id), {"state": "Backlog", "completed_at": None})
            nodes.append(
                self._issue_node(found) | {"state": {"name": held["state"]}, "completedAt": held["completed_at"]}
            )
        return {"issues": _page(nodes)}

    def _TeamProjects(self, variables: dict[str, Any]) -> dict[str, Any]:
        found = self.store.team_projects(self._key(variables["id"]))
        return {"team": {"projects": _page([self._project_node(p) for p in found])}}

    def _Project(self, variables: dict[str, Any]) -> dict[str, Any]:
        found = self.store.project(variables["id"])
        if found is None:
            raise _missing("Project")
        return {"project": self._project_node(found)}

    def _ProjectIssues(self, variables: dict[str, Any]) -> dict[str, Any]:
        if self.store.project(variables["id"]) is None:
            raise _missing("Project")
        nodes = [
            self._issue_node(self.store.issue(i["id"]))
            | {"state": {"name": i["state"]}, "completedAt": i["completed_at"]}
            | self._relations(i["id"])
            for i in self.store.project_issues(variables["id"])
        ]
        return {"project": {"issues": _page(nodes)}}

    def _ProjectDocuments(self, variables: dict[str, Any]) -> dict[str, Any]:
        found = self.store.project_documents(variables["id"])
        nodes = [
            {"id": d["id"], "title": d["title"], "url": d["url"], "content": d["body"], "updatedAt": d["at"]}
            for d in found
        ]
        return {"project": {"documents": _page(nodes)}}

    def _Document(self, variables: dict[str, Any]) -> dict[str, Any]:
        found = self.store.document(variables["id"])
        if found is None:
            raise _missing("Document")
        parent = {"id": found["parent"]}
        on_issue = self.store.issue(found["parent"]) is not None
        node = {"id": found["id"], "title": found["title"], "content": found["body"]}
        return {"document": node | {"issue": parent if on_issue else None, "project": None if on_issue else parent}}

    def _Viewer(self, variables: dict[str, Any]) -> dict[str, Any]:
        return {"viewer": self.store.viewer()}

    def _IssueRelations(self, variables: dict[str, Any]) -> dict[str, Any]:
        held = self._issue(variables["id"])
        return {"issue": {"id": held["id"]} | self._relations(held["id"])}

    def _TeamLabels(self, variables: dict[str, Any]) -> dict[str, Any]:
        names = self.store.repository_labels(self._key(variables["id"]))
        nodes = [{"id": n, "name": n, "isGroup": False, "parent": {"name": GROUP}} for n in names]
        return {"team": {"labels": _page(nodes)}}

    def _IssueCreate(self, variables: dict[str, Any]) -> dict[str, Any]:
        fields = variables["input"]
        self.store.create_issue(
            fields["id"],
            self._key(fields["teamId"]),
            fields["title"],
            fields["description"],
            fields.get("projectId"),
            fields.get("labelIds", []),
        )
        return {"issueCreate": {"success": True, "issue": {"id": fields["id"]}}}

    def _IssueUpdate(self, variables: dict[str, Any]) -> dict[str, Any]:
        id, fields = variables["id"], variables["input"]
        if "labelIds" in fields:
            self.store.set_labels(id, fields["labelIds"])
        if "title" in fields:
            self.store.set_issue_text(id, fields["title"], fields["description"])
        if "assigneeId" in fields:
            self.store.assign(id, fields["assigneeId"])
        return {"issueUpdate": {"success": True}}

    def _DocumentCreate(self, variables: dict[str, Any]) -> dict[str, Any]:
        fields = variables["input"]
        if self.store.document(fields["id"]) is not None:
            raise DuplicateId("INPUT_ERROR", f"{CONFLICT_PREFIX} Document")
        parent = fields.get("issueId") or fields["projectId"]
        self.store.put_document(fields["id"], parent, fields["title"], fields["content"])
        return {"documentCreate": {"success": True, "document": {"id": fields["id"]}}}

    def _DocumentUpdate(self, variables: dict[str, Any]) -> dict[str, Any]:
        held = self.store.document(variables["id"])
        assert held is not None
        fields = variables["input"]
        self.store.put_document(variables["id"], held["parent"], fields["title"], fields["content"])
        return {"documentUpdate": {"success": True}}

    def _IssueRelationCreate(self, variables: dict[str, Any]) -> dict[str, Any]:
        fields = variables["input"]
        self.store.relate(fields["issueId"], fields["relatedIssueId"])
        return {"issueRelationCreate": {"success": True}}

    def _ProjectCreate(self, variables: dict[str, Any]) -> dict[str, Any]:
        fields = variables["input"]
        key = self._key(fields["teamIds"][0])
        self.store.create_project(fields["id"], key, fields["name"], fields["description"], fields.get("labelIds"))
        return {"projectCreate": {"success": True, "project": {"id": fields["id"]}}}

    def _ProjectUpdate(self, variables: dict[str, Any]) -> dict[str, Any]:
        id, fields = variables["id"], variables["input"]
        if "labelIds" in fields:
            self.store.set_project_labels(id, fields["labelIds"])
        if "startDate" in fields:
            self.store.set_project_dates(id, fields["startDate"], fields["targetDate"])
        return {"projectUpdate": {"success": True}}

    def _AttachmentCreate(self, variables: dict[str, Any]) -> dict[str, Any]:
        fields = variables["input"]
        self.store.attach(fields["issueId"], fields["url"], fields["title"], fields["subtitle"], fields["metadata"])
        return {"attachmentCreate": {"success": True}}


MUTATIONS = frozenset(name.removeprefix("_") for name in vars(FakeGraphQL) if name.endswith(("Create", "Update")))
