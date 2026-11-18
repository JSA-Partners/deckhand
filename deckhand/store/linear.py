from __future__ import annotations

from typing import Any

from deckhand.linear import queries
from deckhand.linear.client import DuplicateId, LinearClient, LinearError
from deckhand.linear.workspace import GROUP
from deckhand.records import Record
from deckhand.ref import Ref
from deckhand.step import Refusal
from deckhand.store import Store

RECORD_URL = "https://deckhand.invalid/record/"


def _missing(error: LinearError) -> bool:
    return "not found" in error.message.lower()


def _nodes(connection: dict[str, Any], issue: str, field: str, limit: int = queries.PAGE_SIZE) -> list[dict[str, Any]]:
    if connection["pageInfo"]["hasNextPage"]:
        raise Refusal(f"{issue} has more than {limit} {field}; deckhand cannot read them safely")
    return connection["nodes"]


def _project(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node["id"],
        "name": node["name"],
        "description": node["description"],
        "start_date": node["startDate"],
        "target_date": node["targetDate"],
        "labels": sorted(
            n["name"] for n in _nodes(node["labels"], node["id"], "project labels", queries.PROJECT_LABEL_LIMIT)
        ),
    }


class LinearStore(Store):
    def __init__(self, client: LinearClient) -> None:
        self._client = client

    def _team_id(self, key: str) -> str:
        nodes = self._client.query(queries.TEAM_ID, {"key": key})["teams"]["nodes"]
        if not nodes:
            raise Refusal(f"no Linear team has the key {key}")
        return nodes[0]["id"]

    def _label_ids(self, team_id: str, names: list[str]) -> list[str]:
        if not names:
            return []
        found = self._client.query(queries.LABEL_IDS, {"names": names, "teamId": team_id})
        by_name = {node["name"]: node["id"] for node in found["issueLabels"]["nodes"]}
        for name in names:
            if name not in by_name:
                raise Refusal(f"the team has no label named {name}")
        return [by_name[name] for name in names]

    def _project_label_ids(self, names: list[str]) -> list[str]:
        found = self._client.query(queries.PROJECT_LABEL_IDS, {"names": names})
        by_name = {node["name"]: node["id"] for node in found["projectLabels"]["nodes"]}
        for name in names:
            if name not in by_name:
                raise Refusal(f"the workspace has no project label named {name}; run setup")
        return [by_name[name] for name in names]

    def _issue_node(self, id: str) -> dict[str, Any] | None:
        try:
            return self._client.query(queries.ISSUE, {"id": id})["issue"]
        except LinearError as error:
            if _missing(error):
                return None
            raise

    def _uuid(self, issue: str) -> str:
        try:
            Ref.parse(issue)
        except ValueError:
            return issue
        node = self._issue_node(issue)
        if node is None:
            raise Refusal(f"no Linear issue {issue}")
        return node["id"]

    def create_issue(
        self,
        id: str,
        team: str,
        title: str,
        description: str,
        project: str | None,
        labels: list[str],
    ) -> str:
        team_id = self._team_id(team)
        fields: dict[str, Any] = {
            "id": id,
            "teamId": team_id,
            "title": title,
            "description": description,
        }
        if project is not None:
            fields["projectId"] = project
        label_ids = self._label_ids(team_id, labels)
        if label_ids:
            fields["labelIds"] = label_ids
        self._client.query(queries.ISSUE_CREATE, {"input": fields})
        return id

    def issue(self, id: str) -> dict | None:
        node = self._issue_node(id)
        if node is None:
            return None
        return {
            "id": node["id"],
            "team": node["team"]["key"],
            "number": node["number"],
            "identifier": node["identifier"],
            "title": node["title"],
            "description": node["description"],
            "project": node["project"]["id"] if node["project"] else None,
            "labels": [label["name"] for label in _nodes(node["labels"], id, "labels")],
        }

    def issue_by_ref(self, ref: Ref) -> dict | None:
        return self.issue(str(ref))

    def set_labels(self, issue: str, labels: list[str]) -> None:
        if not labels:
            self._client.query(queries.ISSUE_UPDATE, {"id": self._uuid(issue), "input": {"labelIds": []}})
            return
        node = self._issue_node(issue)
        if node is None:
            raise Refusal(f"no Linear issue {issue}")
        label_ids = self._label_ids(self._team_id(node["team"]["key"]), labels)
        self._client.query(queries.ISSUE_UPDATE, {"id": node["id"], "input": {"labelIds": label_ids}})

    def set_issue_text(self, issue: str, title: str, description: str) -> None:
        fields = {"title": title, "description": description}
        self._client.query(queries.ISSUE_UPDATE, {"id": self._uuid(issue), "input": fields})

    def repository_labels(self, team: str) -> list[str]:
        node = self._client.query(queries.TEAM_LABELS, {"id": self._team_id(team)})["team"]
        found = _nodes(node["labels"], team, "labels")
        return sorted(n["name"] for n in found if (n.get("parent") or {}).get("name") == GROUP)

    def put_document(self, id: str, parent: str, title: str, body: str) -> None:
        node = self._issue_node(parent)
        owner, parent = ("issueId", node["id"]) if node is not None else ("projectId", parent)
        created = {"id": id, owner: parent, "title": title, "content": body}
        try:
            self._client.query(queries.DOCUMENT_CREATE, {"input": created})
        except DuplicateId as error:
            if error.entity != "Document":
                raise
            update = {"title": title, "content": body}
            self._client.query(queries.DOCUMENT_UPDATE, {"id": id, "input": update})

    def document(self, id: str) -> dict | None:
        try:
            node = self._client.query(queries.DOCUMENT, {"id": id})["document"]
        except LinearError as error:
            if _missing(error):
                return None
            raise
        parent = node["issue"] or node["project"]
        return {
            "id": node["id"],
            "parent": parent["id"] if parent else None,
            "title": node["title"],
            "body": node["content"],
        }

    def put_record(self, issue: str, record: Record) -> None:
        attachment = {
            "issueId": self._uuid(issue),
            "url": f"{RECORD_URL}{record.kind}/{record.subject}",
            "title": f"deckhand {record.kind}",
            "metadata": {"record": record.to_json()},
        }
        self._client.query(queries.ATTACHMENT_CREATE, {"input": attachment})

    def records(self, issue: str) -> list[Record]:
        try:
            data = self._client.query(queries.ISSUE_ATTACHMENTS, {"id": issue})
        except LinearError as error:
            if _missing(error):
                return []
            raise
        attachments = _nodes(data["issue"]["attachments"], issue, "attachments")
        held = [n for n in attachments if n["url"].startswith(RECORD_URL)]
        found = []
        for node in sorted(held, key=lambda n: n["url"]):
            try:
                found.append(Record.from_json(node["metadata"]["record"]))
            except (KeyError, TypeError, ValueError) as error:
                raise Refusal(f"{issue} holds a malformed record at {node['url']}: {error}") from None
        return found

    def relate(self, blocker: str, blocked: str) -> None:
        relation = {"issueId": self._uuid(blocker), "relatedIssueId": self._uuid(blocked), "type": "blocks"}
        self._client.query(queries.RELATION_CREATE, {"input": relation})

    def relations(self, issue: str) -> list[tuple[str, str]]:
        try:
            node = self._client.query(queries.ISSUE_RELATIONS, {"id": issue})["issue"]
        except LinearError as error:
            if _missing(error):
                return []
            raise
        outward = _nodes(node["relations"], issue, "relations")
        inward = _nodes(node["inverseRelations"], issue, "inverse relations")
        pairs = [(node["id"], r["relatedIssue"]["id"]) for r in outward if r["type"] == "blocks"]
        pairs += [(r["issue"]["id"], node["id"]) for r in inward if r["type"] == "blocks"]
        return sorted(pairs)

    def create_project(self, id: str, team: str, name: str, description: str, labels: list[str] | None = None) -> str:
        fields = {
            "id": id,
            "name": name,
            "description": description,
            "teamIds": [self._team_id(team)],
        }
        if labels:
            fields["labelIds"] = self._project_label_ids(labels)
        self._client.query(queries.PROJECT_CREATE, {"input": fields})
        return id

    def set_project_labels(self, project: str, labels: list[str]) -> None:
        fields = {"labelIds": self._project_label_ids(labels) if labels else []}
        self._client.query(queries.PROJECT_UPDATE, {"id": project, "input": fields})

    def project(self, id: str) -> dict | None:
        try:
            node = self._client.query(queries.PROJECT, {"id": id})["project"]
        except LinearError as error:
            if _missing(error):
                return None
            raise
        return _project(node)

    def team_projects(self, team: str) -> list[dict]:
        node = self._client.query(queries.TEAM_PROJECTS, {"id": self._team_id(team)})["team"]
        return [_project(n) for n in _nodes(node["projects"], team, "projects")]

    def set_project_dates(self, project: str, start: str | None, target: str | None) -> None:
        dates = {"startDate": start, "targetDate": target}
        self._client.query(queries.PROJECT_UPDATE, {"id": project, "input": dates})

    def project_issues(self, project: str) -> list[dict]:
        try:
            node = self._client.query(queries.PROJECT_ISSUES, {"id": project})["project"]
        except LinearError as error:
            if _missing(error):
                return []
            raise
        return [
            {
                "id": n["id"],
                "team": n["team"]["key"],
                "number": n["number"],
                "identifier": n["identifier"],
                "title": n["title"],
                "description": n["description"],
                "project": project,
                "labels": [label["name"] for label in _nodes(n["labels"], n["id"], "labels")],
                "state": n["state"]["name"],
            }
            for n in _nodes(node["issues"], project, "issues")
        ]
