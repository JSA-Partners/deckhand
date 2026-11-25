from __future__ import annotations

from typing import Any

from deckhand.features import FINISHED
from deckhand.linear import queries
from deckhand.linear.client import DuplicateId, LinearClient, LinearError
from deckhand.linear.workspace import GROUP
from deckhand.records import Record
from deckhand.ref import Ref
from deckhand.step import Refusal
from deckhand.store import Store, require_flat
from deckhand.store.local import LocalRecords


def _missing(error: LinearError) -> bool:
    return "not found" in error.message.lower()


def _nodes(connection: dict[str, Any], issue: str, field: str, limit: int) -> list[dict[str, Any]]:
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
        "finished": bool(node["anyIssue"]["nodes"]) and not node["unfinished"]["nodes"],
    }


def _assignee(node: dict[str, Any]) -> dict[str, Any]:
    owner = node["assignee"]
    return {"assignee": owner["id"] if owner else None, "assignee_name": owner["name"] if owner else None}


class LinearStore(Store):
    def __init__(self, client: LinearClient, records: LocalRecords, teams: dict[str, str] | None = None) -> None:
        self._client = client
        self._records = records
        self._teams = teams if teams is not None else {}
        self._pairs: dict[str, set[tuple[str, str]]] = {}
        self._looked_up: set[str] = set()

    def _paged(self, query: str, variables: dict[str, Any], path: tuple[str, str]) -> list[dict[str, Any]]:
        entity, connection = path
        first = self._client.query(query, {**variables, "after": None})[entity][connection]
        return self._rest(first, query, variables, path)

    def _rest(
        self, page: dict[str, Any], query: str, variables: dict[str, Any], path: tuple[str, str]
    ) -> list[dict[str, Any]]:
        entity, connection = path
        nodes = list(page["nodes"])
        while page["pageInfo"]["hasNextPage"]:
            after = page["pageInfo"]["endCursor"]
            page = self._client.query(query, {**variables, "after": after})[entity][connection]
            nodes += page["nodes"]
        return nodes

    def _labels(self, issue: str, first: dict[str, Any]) -> list[str]:
        nodes = self._rest(first, queries.ISSUE_LABELS, {"id": issue}, ("issue", "labels"))
        return [label["name"] for label in nodes]

    def _look_up_team(self, key: str, configured: bool) -> str:
        nodes = self._client.query(queries.TEAM_ID, {"key": key})["teams"]["nodes"]
        if not nodes:
            raise Refusal(f"no Linear team has the key {key}" + ("; run setup" if configured else ""))
        self._teams[key] = nodes[0]["id"]
        self._looked_up.add(key)
        return self._teams[key]

    def _team_id(self, key: str) -> str:
        return self._teams[key] if key in self._teams else self._look_up_team(key, configured=False)

    def _team_paged(self, key: str, query: str, variables: dict[str, Any], connection: str) -> list[dict[str, Any]]:
        try:
            return self._paged(query, {**variables, "id": self._team_id(key)}, ("team", connection))
        except LinearError as error:
            if not _missing(error) or key in self._looked_up:
                raise
        return self._paged(query, {**variables, "id": self._look_up_team(key, configured=True)}, ("team", connection))

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
        self._pairs[id] = set()
        return id

    def _issue(self, node: dict[str, Any], project: str | None) -> dict[str, Any]:
        return {
            "id": node["id"],
            "team": node["team"]["key"],
            "number": node["number"],
            "identifier": node["identifier"],
            "branch_name": node["branchName"],
            "title": node["title"],
            "description": node["description"],
            "project": project,
            "labels": self._labels(node["id"], node["labels"]),
            **_assignee(node),
        }

    def _state(self, node: dict[str, Any], project: str | None) -> dict[str, Any]:
        return self._issue(node, project) | {"state": node["state"]["name"], "completed_at": node["completedAt"]}

    def issue(self, id: str) -> dict | None:
        node = self._issue_node(id)
        if node is None:
            return None
        return self._issue(node, node["project"]["id"] if node["project"] else None)

    def issues(self, ids: list[str]) -> list[dict]:
        found: dict[str, dict] = {}
        for start in range(0, len(ids), queries.ISSUE_PAGE):
            chunk = ids[start : start + queries.ISSUE_PAGE]
            for node in self._client.query(queries.ISSUES, {"ids": chunk})["issues"]["nodes"]:
                found[node["id"]] = self._state(node, node["project"]["id"] if node["project"] else None)
        return [found[id] for id in ids if id in found]

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
        found = self._team_paged(team, queries.TEAM_LABELS, {}, "labels")
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
        self._records.put_record(issue, record)

    def records(self, issue: str) -> list[Record]:
        return self._records.records(issue)

    def recorded(self, kind: str) -> list[str]:
        return self._records.recorded(kind)

    def attach(self, issue: str, url: str, title: str, subtitle: str, metadata: dict[str, str | int]) -> None:
        require_flat(metadata)
        attachment = {"issueId": issue, "url": url, "title": title, "subtitle": subtitle, "metadata": metadata}
        self._client.query(queries.ATTACHMENT_CREATE, {"input": attachment})

    def relate(self, blocker: str, blocked: str) -> None:
        pair = (self._uuid(blocker), self._uuid(blocked))
        relation = {"issueId": pair[0], "relatedIssueId": pair[1], "type": "blocks"}
        self._client.query(queries.RELATION_CREATE, {"input": relation})
        for end in pair:
            if end in self._pairs:
                self._pairs[end].add(pair)

    def _held_pairs(self, node: dict[str, Any], variables: dict[str, Any]) -> list[tuple[str, str]]:
        outward = self._rest(node["relations"], queries.ISSUE_RELATIONS_PAGE, variables, ("issue", "relations"))
        inward = self._rest(
            node["inverseRelations"], queries.ISSUE_INVERSE_RELATIONS_PAGE, variables, ("issue", "inverseRelations")
        )
        pairs = [(node["id"], r["relatedIssue"]["id"]) for r in outward if r["type"] == "blocks"]
        return pairs + [(r["issue"]["id"], node["id"]) for r in inward if r["type"] == "blocks"]

    def relations(self, issue: str) -> list[tuple[str, str]]:
        if issue in self._pairs:
            return sorted(self._pairs[issue])
        try:
            node = self._client.query(queries.ISSUE_RELATIONS, {"id": issue})["issue"]
        except LinearError as error:
            if _missing(error):
                return []
            raise
        return sorted(self._held_pairs(node, {"id": issue}))

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
            node = self._client.query(queries.PROJECT, {"id": id, "finished": list(FINISHED)})["project"]
        except LinearError as error:
            if _missing(error):
                return None
            raise
        return _project(node)

    def team_projects(self, team: str) -> list[dict]:
        found = self._team_paged(team, queries.TEAM_PROJECTS, {"finished": list(FINISHED)}, "projects")
        return [_project(n) for n in found]

    def set_project_dates(self, project: str, start: str | None, target: str | None) -> None:
        dates = {"startDate": start, "targetDate": target}
        self._client.query(queries.PROJECT_UPDATE, {"id": project, "input": dates})

    def project_issues(self, project: str) -> list[dict]:
        try:
            nodes = self._paged(queries.PROJECT_ISSUES, {"id": project}, ("project", "issues"))
        except LinearError as error:
            if _missing(error):
                return []
            raise
        for node in nodes:
            self._pairs[node["id"]] = set(self._held_pairs(node, {"id": node["id"]}))
        return [self._state(node, project) for node in nodes]

    def project_documents(self, project: str) -> list[dict]:
        try:
            nodes = self._paged(queries.PROJECT_DOCUMENTS, {"id": project}, ("project", "documents"))
        except LinearError as error:
            if _missing(error):
                return []
            raise
        return [
            {"id": n["id"], "title": n["title"], "url": n["url"], "at": n["updatedAt"], "body": n["content"]}
            for n in nodes
        ]

    def assign(self, issue: str, user: str) -> None:
        self._client.query(queries.ISSUE_UPDATE, {"id": self._uuid(issue), "input": {"assigneeId": user}})

    def viewer(self) -> dict:
        node = self._client.query(queries.VIEWER, {})["viewer"]
        return {"id": node["id"], "name": node["name"]}
