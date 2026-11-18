from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from deckhand.linear import queries
from deckhand.linear.client import DuplicateId, LinearClient
from deckhand.ops import create_id
from deckhand.step import Refusal

GROUP = "Repository"
COLORS = {
    "Backlog": "#bec2c8",
    "In Progress": "#f2c94c",
    "In Review": "#5e6ad2",
    "Done": "#4cb782",
    "Canceled": "#95a2b3",
    "Duplicate": "#95a2b3",
}
STATES = {
    "Backlog": "backlog",
    "In Progress": "started",
    "In Review": "started",
    "Done": "completed",
    "Canceled": "canceled",
    "Duplicate": "duplicate",
}
AUTOMATION = {"draft": "In Progress", "start": "In Review", "merge": "Done"}
ARCHIVE_MONTHS = 1
PAGE_SIZE = 250


@dataclass(frozen=True)
class Change:
    text: str
    run: Callable[[dict[str, str]], None]


class Workspace:
    def __init__(self, client: LinearClient) -> None:
        self._client = client

    def team(self, key: str) -> dict[str, Any] | None:
        nodes = self._client.query(queries.TEAM_BY_KEY, {"key": key})["teams"]["nodes"]
        if not nodes:
            return None
        team = dict(nodes[0])
        team["states"] = self._page(queries.TEAM_STATES, team["id"], "states")
        team["labels"] = self._page(queries.TEAM_LABELS, team["id"], "labels")
        team["gitAutomationStates"] = self._page(queries.TEAM_AUTOMATION, team["id"], "gitAutomationStates")
        return team

    def _page(self, document: str, team_id: str, field: str) -> dict[str, Any]:
        connection = self._client.query(document, {"id": team_id})["team"][field]
        if connection["pageInfo"]["hasNextPage"]:
            raise Refusal(f"the team has more than {PAGE_SIZE} {field}; setup cannot plan safely")
        return connection

    def plan(self, team: dict[str, Any], labels: list[str]) -> list[Change]:
        team_id = team["id"]
        changes: list[Change] = []
        held = {state["name"]: state["id"] for state in team["states"]["nodes"]}

        def create_state(name: str) -> Callable[[dict[str, str]], None]:
            def run(ids: dict[str, str]) -> None:
                input = {"teamId": team_id, "name": name, "type": STATES[name], "color": COLORS[name]}
                self._client.query(queries.WORKFLOW_STATE_CREATE, {"input": input})
                ids[name] = self._state_id(team_id, name)

            return run

        for name in STATES:
            if name not in held:
                changes.append(Change(f"create state {name}", create_state(name)))
        changes += self._automation(team, held)
        changes += self._labels(team, labels)
        if team["autoArchivePeriod"] != ARCHIVE_MONTHS or team["autoCloseParentIssues"] is not True:
            settings = {"autoArchivePeriod": ARCHIVE_MONTHS, "autoCloseParentIssues": True}
            changes.append(
                Change(
                    "set team auto-archive to 1 month and auto-close of parent issues",
                    lambda ids: self._client.query(queries.TEAM_UPDATE, {"id": team_id, "input": settings}),
                )
            )
        return changes

    def state_ids(self, team: dict[str, Any]) -> dict[str, str]:
        return {state["name"]: state["id"] for state in team["states"]["nodes"]}

    def _state_id(self, team_id: str, name: str) -> str:
        states = self._page(queries.TEAM_STATES, team_id, "states")
        return self.state_ids({"states": states})[name]

    def _automation(self, team: dict[str, Any], held: dict[str, str]) -> list[Change]:
        rows = {row["event"]: row for row in team["gitAutomationStates"]["nodes"]}
        changes = []
        for event, target in AUTOMATION.items():
            row = rows.get(event)
            if row is not None and (row["state"] or {}).get("name") == target:
                continue

            def run(ids: dict[str, str], event=event, target=target, row=row) -> None:
                if row is None:
                    input = {"teamId": team["id"], "stateId": ids[target], "event": event}
                    self._client.query(queries.GIT_AUTOMATION_STATE_CREATE, {"input": input})
                else:
                    self._client.query(
                        queries.GIT_AUTOMATION_STATE_UPDATE, {"id": row["id"], "input": {"stateId": ids[target]}}
                    )

            verb = "create" if row is None else "update"
            changes.append(Change(f"{verb} GitHub automation {event} to {target}", run))
        return changes

    def _labels(self, team: dict[str, Any], names: list[str]) -> list[Change]:
        found = team["labels"]["nodes"]
        group = next((label for label in found if label["name"] == GROUP and not label.get("parent")), None)
        if group is not None and not group["isGroup"]:
            raise Refusal(f"the label {GROUP} exists but is not a group; rename or remove it first")
        group_id = group["id"] if group else create_id(team["id"], f"label-group:{GROUP}")
        children = {label["name"] for label in found if (label.get("parent") or {}).get("name") == GROUP}
        changes = []
        if group is None:
            changes.append(
                Change(
                    f"create label group {GROUP}",
                    lambda ids: self._create_label(
                        {"id": group_id, "teamId": team["id"], "name": GROUP, "isGroup": True}
                    ),
                )
            )
        for name in names:
            if name in children:
                continue
            fields = {"id": create_id(team["id"], f"label:{name}"), "teamId": team["id"], "name": name}
            fields["parentId"] = group_id
            changes.append(Change(f"create label {name} under {GROUP}", lambda ids, f=fields: self._create_label(f)))
        return changes

    def _create_label(self, fields: dict[str, Any]) -> None:
        try:
            self._client.query(queries.ISSUE_LABEL_CREATE, {"input": fields})
        except DuplicateId:
            pass

    def apply(self, team: dict[str, Any], changes: list[Change]) -> None:
        ids = self.state_ids(team)
        for change in changes:
            change.run(ids)
