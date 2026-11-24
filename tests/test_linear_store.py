from __future__ import annotations

import re
from pathlib import Path

import pytest

from deckhand.linear import queries
from deckhand.linear.client import DuplicateId, LinearError
from deckhand.ops import ensure_relation
from deckhand.records import Record
from deckhand.ref import Ref
from deckhand.step import Refusal
from deckhand.store.linear import LinearStore
from deckhand.store.local import LocalRecords

UUID = "3f2b8c9e-7a41-4d55-9c1e-0a1b2c3d4e5f"
TEAM_ID = "team-1"
MORE = {"hasNextPage": True}
DONE = {"hasNextPage": False}


class FakeClient:
    def __init__(self, **responses: object) -> None:
        self.responses = {name: list(v) if isinstance(v, list) else [v] for name, v in responses.items()}
        self.calls: list[tuple[str, dict]] = []
        self.documents: list[str] = []

    def query(self, document: str, variables: dict) -> dict:
        match = re.search(r"(?:query|mutation) (\w+)", document)
        assert match is not None
        name = match.group(1)
        self.calls.append((name, variables))
        self.documents.append(document)
        response = self.responses[name].pop(0) if len(self.responses[name]) > 1 else self.responses[name][0]
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def duplicate(entity: str) -> DuplicateId:
    return DuplicateId("INPUT_ERROR", f"conflict on insert of {entity}")


ISSUE_NODE = {
    "id": UUID,
    "identifier": "ACME-5",
    "number": 5,
    "branchName": "acme-5-title",
    "title": "Title",
    "description": "body",
    "team": {"key": "ACME"},
    "project": {"id": "proj-1"},
    "assignee": {"id": "user-1", "name": "Ada"},
    "labels": {"nodes": [{"id": "l1", "name": "deckhand"}], "pageInfo": {"hasNextPage": False}},
}


@pytest.fixture
def records(tmp_path: Path) -> LocalRecords:
    return LocalRecords(tmp_path / "deckhand.db")


def record(subject: str = "plan", kind: str = "approval") -> Record:
    return Record(kind, subject, "v1", "captain", {"ok": True})


def test_create_issue_resolves_team_and_labels(records: LocalRecords) -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        LabelIds={"issueLabels": {"nodes": [{"id": "l1", "name": "deckhand"}]}},
        IssueCreate={"issueCreate": {"success": True, "issue": {"id": UUID}}},
    )
    store = LinearStore(client, records)
    assert store.create_issue(UUID, "ACME", "Title", "body", "proj-1", ["deckhand"]) == UUID
    assert client.calls == [
        ("TeamId", {"key": "ACME"}),
        ("LabelIds", {"names": ["deckhand"], "teamId": TEAM_ID}),
        (
            "IssueCreate",
            {
                "input": {
                    "id": UUID,
                    "teamId": TEAM_ID,
                    "title": "Title",
                    "description": "body",
                    "projectId": "proj-1",
                    "labelIds": ["l1"],
                }
            },
        ),
    ]


def test_create_issue_without_project_or_labels_omits_them(records: LocalRecords) -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        IssueCreate={"issueCreate": {"success": True, "issue": {"id": UUID}}},
    )
    LinearStore(client, records).create_issue(UUID, "ACME", "T", "b", None, [])
    assert client.names == ["TeamId", "IssueCreate"]
    assert client.calls[1][1]["input"] == {"id": UUID, "teamId": TEAM_ID, "title": "T", "description": "b"}


def test_create_issue_repeat_id_raises_duplicate(records: LocalRecords) -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        IssueCreate=duplicate("Issue"),
    )
    with pytest.raises(DuplicateId):
        LinearStore(client, records).create_issue(UUID, "ACME", "T", "b", None, [])


def test_create_issue_unknown_team_is_refused(records: LocalRecords) -> None:
    client = FakeClient(TeamId={"teams": {"nodes": []}})
    with pytest.raises(Refusal, match="ACME"):
        LinearStore(client, records).create_issue(UUID, "ACME", "T", "b", None, [])
    assert client.names == ["TeamId"]


def test_create_issue_unknown_label_is_refused_naming_it(records: LocalRecords) -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        LabelIds={"issueLabels": {"nodes": [{"id": "l1", "name": "deckhand"}]}},
    )
    with pytest.raises(Refusal, match="ghost"):
        LinearStore(client, records).create_issue(UUID, "ACME", "T", "b", None, ["deckhand", "ghost"])
    assert "IssueCreate" not in client.names


def test_issue_maps_the_node(records: LocalRecords) -> None:
    client = FakeClient(Issue={"issue": ISSUE_NODE})
    assert LinearStore(client, records).issue(UUID) == {
        "id": UUID,
        "team": "ACME",
        "number": 5,
        "identifier": "ACME-5",
        "branch_name": "acme-5-title",
        "title": "Title",
        "description": "body",
        "project": "proj-1",
        "labels": ["deckhand"],
        "assignee": "user-1",
        "assignee_name": "Ada",
    }
    assert client.calls == [("Issue", {"id": UUID})]


def test_issue_without_project_maps_none(records: LocalRecords) -> None:
    client = FakeClient(Issue={"issue": {**ISSUE_NODE, "project": None}})
    assert LinearStore(client, records).issue(UUID)["project"] is None


def test_issue_not_found_is_none(records: LocalRecords) -> None:
    client = FakeClient(Issue=LinearError("INPUT_ERROR", "Entity not found: Issue"))
    assert LinearStore(client, records).issue(UUID) is None


def test_issue_other_errors_propagate(records: LocalRecords) -> None:
    client = FakeClient(Issue=LinearError("network", "could not reach Linear"))
    with pytest.raises(LinearError):
        LinearStore(client, records).issue(UUID)


def test_issue_by_ref_queries_by_identifier(records: LocalRecords) -> None:
    client = FakeClient(Issue={"issue": ISSUE_NODE})
    found = LinearStore(client, records).issue_by_ref(Ref("ACME", 5))
    assert found is not None and found["id"] == UUID
    assert client.calls == [("Issue", {"id": "ACME-5"})]


def test_set_labels_resolves_ids_in_the_issue_team(records: LocalRecords) -> None:
    client = FakeClient(
        Issue={"issue": ISSUE_NODE},
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        LabelIds={"issueLabels": {"nodes": [{"id": "l2", "name": "ready"}]}},
        IssueUpdate={"issueUpdate": {"success": True}},
    )
    LinearStore(client, records).set_labels(UUID, ["ready"])
    assert client.calls == [
        ("Issue", {"id": UUID}),
        ("TeamId", {"key": "ACME"}),
        ("LabelIds", {"names": ["ready"], "teamId": TEAM_ID}),
        ("IssueUpdate", {"id": UUID, "input": {"labelIds": ["l2"]}}),
    ]


def test_workspace_label_survives_repository_swap(records: LocalRecords) -> None:
    workspace_label = {"id": "w1", "name": "Urgent"}
    client = FakeClient(
        Issue={"issue": ISSUE_NODE},
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        LabelIds={"issueLabels": {"nodes": [{"id": "l2", "name": "gadgets"}, workspace_label]}},
        IssueUpdate={"issueUpdate": {"success": True}},
    )
    LinearStore(client, records).set_labels(UUID, ["gadgets", "Urgent"])
    assert client.calls[-1] == ("IssueUpdate", {"id": UUID, "input": {"labelIds": ["l2", "w1"]}})
    assert "null: true" in client.documents[2]


def test_set_labels_empty_clears_without_lookup(records: LocalRecords) -> None:
    client = FakeClient(IssueUpdate={"issueUpdate": {"success": True}})
    LinearStore(client, records).set_labels(UUID, [])
    assert client.calls == [("IssueUpdate", {"id": UUID, "input": {"labelIds": []}})]


def test_put_document_on_an_issue_creates(records: LocalRecords) -> None:
    client = FakeClient(
        Issue={"issue": ISSUE_NODE},
        DocumentCreate={"documentCreate": {"success": True, "document": {"id": "d1"}}},
    )
    LinearStore(client, records).put_document("d1", UUID, "Plan", "# Plan")
    assert client.calls == [
        ("Issue", {"id": UUID}),
        ("DocumentCreate", {"input": {"id": "d1", "issueId": UUID, "title": "Plan", "content": "# Plan"}}),
    ]


def test_put_document_on_a_project_creates(records: LocalRecords) -> None:
    client = FakeClient(
        Issue=LinearError("INPUT_ERROR", "Entity not found: Issue"),
        DocumentCreate={"documentCreate": {"success": True, "document": {"id": "d1"}}},
    )
    LinearStore(client, records).put_document("d1", "proj-1", "Plan", "# Plan")
    assert client.calls[1] == (
        "DocumentCreate",
        {"input": {"id": "d1", "projectId": "proj-1", "title": "Plan", "content": "# Plan"}},
    )


def test_put_document_repeat_id_updates(records: LocalRecords) -> None:
    client = FakeClient(
        Issue={"issue": ISSUE_NODE},
        DocumentCreate=duplicate("Document"),
        DocumentUpdate={"documentUpdate": {"success": True}},
    )
    LinearStore(client, records).put_document("d1", UUID, "Plan", "# New")
    assert client.calls[-1] == ("DocumentUpdate", {"id": "d1", "input": {"title": "Plan", "content": "# New"}})


def test_put_document_other_duplicate_entity_propagates(records: LocalRecords) -> None:
    client = FakeClient(Issue={"issue": ISSUE_NODE}, DocumentCreate=duplicate("Issue"))
    with pytest.raises(DuplicateId):
        LinearStore(client, records).put_document("d1", UUID, "Plan", "x")


def test_document_maps_the_node(records: LocalRecords) -> None:
    node = {"id": "d1", "title": "Plan", "content": "# Plan", "issue": {"id": UUID}, "project": None}
    client = FakeClient(Document={"document": node})
    assert LinearStore(client, records).document("d1") == {
        "id": "d1",
        "parent": UUID,
        "title": "Plan",
        "body": "# Plan",
    }
    assert client.calls == [("Document", {"id": "d1"})]


def test_document_of_a_project_names_the_project(records: LocalRecords) -> None:
    node = {"id": "d1", "title": "Plan", "content": "x", "issue": None, "project": {"id": "proj-1"}}
    client = FakeClient(Document={"document": node})
    assert LinearStore(client, records).document("d1")["parent"] == "proj-1"


def test_document_not_found_is_none(records: LocalRecords) -> None:
    client = FakeClient(Document=LinearError("INPUT_ERROR", "Entity not found: Document"))
    assert LinearStore(client, records).document("d1") is None


def test_put_record_sends_nothing_to_linear(tmp_path: Path) -> None:
    client = FakeClient()
    store = LinearStore(client, LocalRecords(tmp_path / "deckhand.db"))
    store.put_record(UUID, record("plan"))
    store.put_record(UUID, record("review"))
    assert [r.subject for r in store.records(UUID)] == ["plan", "review"]
    assert client.calls == []


def test_relate_sends_blocker_as_issue_and_blocked_as_related(records: LocalRecords) -> None:
    client = FakeClient(IssueRelationCreate={"issueRelationCreate": {"success": True}})
    LinearStore(client, records).relate("a", "b")
    assert client.calls == [
        ("IssueRelationCreate", {"input": {"issueId": "a", "relatedIssueId": "b", "type": "blocks"}})
    ]


def test_relations_reads_both_directions_blocks_only_sorted(records: LocalRecords) -> None:
    node = {
        "id": "m",
        "relations": {
            "nodes": [
                {"type": "blocks", "relatedIssue": {"id": "z"}},
                {"type": "related", "relatedIssue": {"id": "q"}},
            ],
            "pageInfo": DONE,
        },
        "inverseRelations": {
            "nodes": [
                {"type": "blocks", "issue": {"id": "a"}},
                {"type": "duplicate", "issue": {"id": "p"}},
            ],
            "pageInfo": DONE,
        },
    }
    client = FakeClient(IssueRelations={"issue": node})
    assert LinearStore(client, records).relations("m") == [("a", "m"), ("m", "z")]
    assert client.calls == [("IssueRelations", {"id": "m"})]


def test_relations_of_a_missing_issue_is_empty(records: LocalRecords) -> None:
    client = FakeClient(IssueRelations=LinearError("INPUT_ERROR", "Entity not found: Issue"))
    assert LinearStore(client, records).relations("m") == []


def relations_node(relations: dict = DONE, inverse: dict = DONE) -> dict:
    return {
        "id": "m",
        "relations": {"nodes": [], "pageInfo": relations},
        "inverseRelations": {"nodes": [], "pageInfo": inverse},
    }


@pytest.mark.parametrize(
    ("name", "response", "read"),
    [
        ("Issue", {"issue": {**ISSUE_NODE, "labels": {"nodes": [], "pageInfo": MORE}}}, "issue"),
        ("IssueRelations", {"issue": relations_node(relations=MORE)}, "relations"),
        ("IssueRelations", {"issue": relations_node(inverse=MORE)}, "relations"),
    ],
)
def test_a_page_overflow_refuses_naming_the_issue(name: str, response: dict, read: str, records: LocalRecords) -> None:
    client = FakeClient(**{name: response})
    with pytest.raises(Refusal, match=rf"{UUID}.*more than {queries.PAGE_SIZE}"):
        getattr(LinearStore(client, records), read)(UUID)


OTHER = "7c1d2e3f-4a5b-4c6d-8e7f-901a2b3c4d5e"
OTHER_NODE = {**ISSUE_NODE, "id": OTHER, "identifier": "ACME-6", "number": 6}


def test_set_labels_writes_the_uuid_whichever_form_is_passed(records: LocalRecords) -> None:
    for form in (UUID, "ACME-5"):
        client = FakeClient(Issue={"issue": ISSUE_NODE}, IssueUpdate={"issueUpdate": {"success": True}})
        LinearStore(client, records).set_labels(form, [])
        assert client.calls[-1] == ("IssueUpdate", {"id": UUID, "input": {"labelIds": []}})


def test_relate_writes_uuids_for_identifiers(records: LocalRecords) -> None:
    client = FakeClient(
        Issue=[{"issue": ISSUE_NODE}, {"issue": OTHER_NODE}],
        IssueRelationCreate={"issueRelationCreate": {"success": True}},
    )
    LinearStore(client, records).relate("ACME-5", "ACME-6")
    writes = [variables["input"] for name, variables in client.calls if name != "Issue"]
    assert (writes[0]["issueId"], writes[0]["relatedIssueId"]) == (UUID, OTHER)


def test_ensure_relation_writes_nothing_when_held_whichever_form_is_passed(records: LocalRecords) -> None:
    held = {
        "issue": {
            "id": UUID,
            "relations": {"nodes": [{"type": "blocks", "relatedIssue": {"id": OTHER}}], "pageInfo": DONE},
            "inverseRelations": {"nodes": [], "pageInfo": DONE},
        }
    }
    for blocker, blocked in ((UUID, OTHER), ("ACME-5", "ACME-6")):
        client = FakeClient(Issue=[{"issue": ISSUE_NODE}, {"issue": OTHER_NODE}], IssueRelations=held)
        ensure_relation(LinearStore(client, records), blocker, blocked)
        assert "IssueRelationCreate" not in client.names


def test_an_unknown_identifier_refuses_before_any_write(records: LocalRecords) -> None:
    client = FakeClient(Issue=LinearError("INPUT_ERROR", "Entity not found: Issue"))
    with pytest.raises(Refusal, match="ACME-99"):
        LinearStore(client, records).relate("ACME-99", UUID)
    assert client.names == ["Issue"]


def test_issue_reads_ask_linear_for_a_full_page(records: LocalRecords) -> None:
    client = FakeClient(
        Issue={"issue": ISSUE_NODE},
        IssueRelations={"issue": relations_node()},
    )
    store = LinearStore(client, records)
    store.issue(UUID)
    store.relations(UUID)
    for document in client.documents:
        assert f"(first: {queries.PAGE_SIZE}" in document
    assert client.documents[1].count(f"(first: {queries.PAGE_SIZE})") == 2


PROJECT_NODE = {
    "id": "proj-1",
    "name": "Feature",
    "description": "about",
    "startDate": "2026-11-01",
    "targetDate": None,
    "labels": {"nodes": [{"name": "deckhand"}], "pageInfo": {"hasNextPage": False}},
}
PROJECT = {
    "id": "proj-1",
    "name": "Feature",
    "description": "about",
    "start_date": "2026-11-01",
    "target_date": None,
    "labels": ["deckhand"],
}


def test_create_project_resolves_the_team_and_sends_teamids(records: LocalRecords) -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        ProjectCreate={"projectCreate": {"success": True, "project": {"id": "proj-1"}}},
    )
    assert LinearStore(client, records).create_project("proj-1", "ACME", "Feature", "about") == "proj-1"
    assert client.calls == [
        ("TeamId", {"key": "ACME"}),
        (
            "ProjectCreate",
            {"input": {"id": "proj-1", "name": "Feature", "description": "about", "teamIds": [TEAM_ID]}},
        ),
    ]


def test_create_project_sends_project_label_ids(records: LocalRecords) -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        ProjectLabelIds={"projectLabels": {"nodes": [{"id": "pl1", "name": "deckhand"}]}},
        ProjectCreate={"projectCreate": {"success": True, "project": {"id": "proj-1"}}},
    )
    LinearStore(client, records).create_project("proj-1", "ACME", "Feature", "about", ["deckhand"])
    assert client.calls[1:] == [
        ("ProjectLabelIds", {"names": ["deckhand"]}),
        (
            "ProjectCreate",
            {
                "input": {
                    "id": "proj-1",
                    "name": "Feature",
                    "description": "about",
                    "teamIds": [TEAM_ID],
                    "labelIds": ["pl1"],
                }
            },
        ),
    ]


def test_set_project_labels_refuses_a_label_the_workspace_lacks(records: LocalRecords) -> None:
    client = FakeClient(ProjectLabelIds={"projectLabels": {"nodes": []}})
    with pytest.raises(Refusal, match="no project label named deckhand"):
        LinearStore(client, records).set_project_labels("proj-1", ["deckhand"])
    assert client.names == ["ProjectLabelIds"]


def test_set_project_labels_replaces_the_list(records: LocalRecords) -> None:
    client = FakeClient(
        ProjectLabelIds={"projectLabels": {"nodes": [{"id": "pl1", "name": "deckhand"}]}},
        ProjectUpdate={"projectUpdate": {"success": True}},
    )
    LinearStore(client, records).set_project_labels("proj-1", ["deckhand"])
    assert client.calls[-1] == ("ProjectUpdate", {"id": "proj-1", "input": {"labelIds": ["pl1"]}})


def test_project_past_one_page_of_labels_refuses(records: LocalRecords) -> None:
    node = {**PROJECT_NODE, "labels": {"nodes": [], "pageInfo": MORE}}
    client = FakeClient(Project={"project": node})
    with pytest.raises(Refusal, match="more than 10 project labels"):
        LinearStore(client, records).project("proj-1")


def test_create_project_repeat_id_raises_duplicate(records: LocalRecords) -> None:
    client = FakeClient(TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}}, ProjectCreate=duplicate("Project"))
    with pytest.raises(DuplicateId) as caught:
        LinearStore(client, records).create_project("proj-1", "ACME", "Feature", "about")
    assert caught.value.entity == "Project"


def test_project_maps_the_node(records: LocalRecords) -> None:
    client = FakeClient(Project={"project": PROJECT_NODE})
    assert LinearStore(client, records).project("proj-1") == PROJECT
    assert client.calls == [("Project", {"id": "proj-1"})]


def test_project_not_found_is_none(records: LocalRecords) -> None:
    client = FakeClient(Project=LinearError("INPUT_ERROR", "Entity not found: Project"))
    assert LinearStore(client, records).project("proj-1") is None


def test_project_other_errors_propagate(records: LocalRecords) -> None:
    client = FakeClient(Project=LinearError("INPUT_ERROR", "bad"))
    with pytest.raises(LinearError):
        LinearStore(client, records).project("proj-1")


def test_team_projects_queries_the_team_and_maps_nodes(records: LocalRecords) -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        TeamProjects={"team": {"projects": {"nodes": [PROJECT_NODE], "pageInfo": DONE}}},
    )
    assert LinearStore(client, records).team_projects("ACME") == [PROJECT]
    assert client.calls[1] == ("TeamProjects", {"id": TEAM_ID})
    assert f"(first: {queries.PAGE_SIZE})" in client.documents[1]


def test_team_projects_overflow_refuses(records: LocalRecords) -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        TeamProjects={"team": {"projects": {"nodes": [], "pageInfo": MORE}}},
    )
    with pytest.raises(Refusal, match=rf"ACME.*more than {queries.PAGE_SIZE}"):
        LinearStore(client, records).team_projects("ACME")


def test_set_project_dates_sends_both_dates_and_null_clears(records: LocalRecords) -> None:
    client = FakeClient(ProjectUpdate={"projectUpdate": {"success": True}})
    store = LinearStore(client, records)
    store.set_project_dates("proj-1", "2026-11-01", "2026-12-01")
    store.set_project_dates("proj-1", None, None)
    assert client.calls == [
        ("ProjectUpdate", {"id": "proj-1", "input": {"startDate": "2026-11-01", "targetDate": "2026-12-01"}}),
        ("ProjectUpdate", {"id": "proj-1", "input": {"startDate": None, "targetDate": None}}),
    ]


def test_project_issues_maps_nodes_with_state(records: LocalRecords) -> None:
    node = {
        "id": UUID,
        "identifier": "ACME-5",
        "number": 5,
        "branchName": "acme-5-title",
        "title": "Title",
        "description": "body",
        "team": {"key": "ACME"},
        "state": {"name": "Done"},
        "completedAt": "2026-11-20T09:30:00.000Z",
        "assignee": {"id": "user-1", "name": "Ada"},
        "labels": {"nodes": [{"name": "deckhand"}], "pageInfo": DONE},
    }
    client = FakeClient(ProjectIssues={"project": {"issues": {"nodes": [node], "pageInfo": DONE}}})
    assert LinearStore(client, records).project_issues("proj-1") == [
        {
            "id": UUID,
            "team": "ACME",
            "number": 5,
            "identifier": "ACME-5",
            "branch_name": "acme-5-title",
            "title": "Title",
            "description": "body",
            "project": "proj-1",
            "labels": ["deckhand"],
            "assignee": "user-1",
            "assignee_name": "Ada",
            "state": "Done",
            "completed_at": "2026-11-20T09:30:00.000Z",
        }
    ]
    assert client.calls == [("ProjectIssues", {"id": "proj-1", "after": None})]
    assert f"(first: {queries.PAGE_SIZE})" in client.documents[0]
    assert "completedAt" in client.documents[0]


def issue_node(number: int, assignee: dict | None = None) -> dict:
    return {
        "id": f"id-{number}",
        "identifier": f"ACME-{number}",
        "number": number,
        "branchName": f"acme-{number}-t",
        "title": "T",
        "description": "",
        "team": {"key": "ACME"},
        "state": {"name": "Done"},
        "completedAt": None,
        "assignee": assignee,
        "labels": {"nodes": [], "pageInfo": DONE},
    }


def test_project_issues_read_across_pages(records: LocalRecords) -> None:
    client = FakeClient(
        ProjectIssues=[
            {"project": {"issues": {"nodes": [issue_node(1)], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}},
            {"project": {"issues": {"nodes": [issue_node(2)], "pageInfo": DONE}}},
        ]
    )
    found = LinearStore(client, records).project_issues("proj-1")
    assert [i["number"] for i in found] == [1, 2]
    assert found[0]["assignee"] is None
    assert found[0]["assignee_name"] is None
    assert found[0]["completed_at"] is None
    assert client.calls[1] == ("ProjectIssues", {"id": "proj-1", "after": "c1"})


def test_project_issues_label_overflow_refuses(records: LocalRecords) -> None:
    node = {
        "id": UUID,
        "identifier": "ACME-5",
        "number": 5,
        "branchName": "acme-5-t",
        "title": "T",
        "description": "",
        "team": {"key": "ACME"},
        "state": {"name": "Done"},
        "completedAt": None,
        "assignee": None,
        "labels": {"nodes": [], "pageInfo": MORE},
    }
    client = FakeClient(ProjectIssues={"project": {"issues": {"nodes": [node], "pageInfo": DONE}}})
    with pytest.raises(Refusal, match=rf"{UUID}.*more than {queries.PAGE_SIZE}"):
        LinearStore(client, records).project_issues("proj-1")


def test_project_issues_of_a_missing_project_is_empty(records: LocalRecords) -> None:
    client = FakeClient(ProjectIssues=LinearError("INPUT_ERROR", "Entity not found: Project"))
    assert LinearStore(client, records).project_issues("proj-1") == []


def test_set_issue_text_updates_title_and_description(records: LocalRecords) -> None:
    client = FakeClient(IssueUpdate={"issueUpdate": {"success": True}})
    LinearStore(client, records).set_issue_text(UUID, "New", "Body")
    assert client.calls == [("IssueUpdate", {"id": UUID, "input": {"title": "New", "description": "Body"}})]


def test_repository_labels_lists_the_children_of_the_group(records: LocalRecords) -> None:
    nodes = [
        {"id": "g", "name": "Repository", "isGroup": True, "parent": None},
        {"id": "l1", "name": "widgets", "isGroup": False, "parent": {"name": "Repository"}},
        {"id": "l2", "name": "ready", "isGroup": False, "parent": None},
    ]
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        TeamLabels={"team": {"labels": {"nodes": nodes, "pageInfo": DONE}}},
    )
    assert LinearStore(client, records).repository_labels("ACME") == ["widgets"]


def test_issue_carries_the_assignee(records: LocalRecords) -> None:
    client = FakeClient(Issue={"issue": ISSUE_NODE})
    found = LinearStore(client, records).issue(UUID)
    assert found["assignee"] == "user-1"
    assert found["assignee_name"] == "Ada"


def test_an_unassigned_issue_has_no_assignee(records: LocalRecords) -> None:
    client = FakeClient(Issue={"issue": {**ISSUE_NODE, "assignee": None}})
    found = LinearStore(client, records).issue(UUID)
    assert found["assignee"] is None
    assert found["assignee_name"] is None


def test_assign_round_trip(records: LocalRecords) -> None:
    client = FakeClient(IssueUpdate={"issueUpdate": {"success": True}}, Issue={"issue": ISSUE_NODE})
    store = LinearStore(client, records)
    store.assign("ACME-5", "user-1")
    assert client.calls[-1] == ("IssueUpdate", {"id": UUID, "input": {"assigneeId": "user-1"}})
    assert store.issue(UUID)["assignee"] == "user-1"


def test_viewer(records: LocalRecords) -> None:
    client = FakeClient(Viewer={"viewer": {"id": "user-1", "name": "Ada"}})
    assert LinearStore(client, records).viewer() == {"id": "user-1", "name": "Ada"}


def test_project_documents_listed_across_pages(records: LocalRecords) -> None:
    client = FakeClient(
        ProjectDocuments=[
            {
                "project": {
                    "documents": {
                        "nodes": [
                            {
                                "id": "d1",
                                "title": "One",
                                "url": "https://linear.app/d1",
                                "updatedAt": "2026-11-19T10:00:00.000Z",
                            }
                        ],
                        "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    }
                }
            },
            {
                "project": {
                    "documents": {
                        "nodes": [
                            {
                                "id": "d2",
                                "title": "Two",
                                "url": "https://linear.app/d2",
                                "updatedAt": "2026-11-19T11:00:00.000Z",
                            }
                        ],
                        "pageInfo": DONE,
                    }
                }
            },
        ]
    )
    assert LinearStore(client, records).project_documents("proj-1") == [
        {"id": "d1", "title": "One", "url": "https://linear.app/d1", "at": "2026-11-19T10:00:00.000Z"},
        {"id": "d2", "title": "Two", "url": "https://linear.app/d2", "at": "2026-11-19T11:00:00.000Z"},
    ]
    assert client.calls[1] == ("ProjectDocuments", {"id": "proj-1", "after": "c1"})


def test_project_documents_of_a_missing_project_is_empty(records: LocalRecords) -> None:
    client = FakeClient(ProjectDocuments=LinearError("INPUT_ERROR", "Entity not found: Project"))
    assert LinearStore(client, records).project_documents("proj-1") == []
