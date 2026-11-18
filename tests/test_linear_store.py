from __future__ import annotations

import re

import pytest

from deckhand.linear import queries
from deckhand.linear.client import DuplicateId, LinearError
from deckhand.ops import ensure_relation
from deckhand.records import Record
from deckhand.ref import Ref
from deckhand.step import Refusal
from deckhand.store.linear import LinearStore

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
    "title": "Title",
    "description": "body",
    "team": {"key": "ACME"},
    "project": {"id": "proj-1"},
    "labels": {"nodes": [{"id": "l1", "name": "deckhand"}], "pageInfo": {"hasNextPage": False}},
}


def record(subject: str = "plan", kind: str = "approval") -> Record:
    return Record(kind, subject, "v1", "captain", {"ok": True})


def test_create_issue_resolves_team_and_labels() -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        LabelIds={"issueLabels": {"nodes": [{"id": "l1", "name": "deckhand"}]}},
        IssueCreate={"issueCreate": {"success": True, "issue": {"id": UUID}}},
    )
    store = LinearStore(client)
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


def test_create_issue_without_project_or_labels_omits_them() -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        IssueCreate={"issueCreate": {"success": True, "issue": {"id": UUID}}},
    )
    LinearStore(client).create_issue(UUID, "ACME", "T", "b", None, [])
    assert client.names == ["TeamId", "IssueCreate"]
    assert client.calls[1][1]["input"] == {"id": UUID, "teamId": TEAM_ID, "title": "T", "description": "b"}


def test_create_issue_repeat_id_raises_duplicate() -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        IssueCreate=duplicate("Issue"),
    )
    with pytest.raises(DuplicateId):
        LinearStore(client).create_issue(UUID, "ACME", "T", "b", None, [])


def test_create_issue_unknown_team_is_refused() -> None:
    client = FakeClient(TeamId={"teams": {"nodes": []}})
    with pytest.raises(Refusal, match="ACME"):
        LinearStore(client).create_issue(UUID, "ACME", "T", "b", None, [])
    assert client.names == ["TeamId"]


def test_create_issue_unknown_label_is_refused_naming_it() -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        LabelIds={"issueLabels": {"nodes": [{"id": "l1", "name": "deckhand"}]}},
    )
    with pytest.raises(Refusal, match="ghost"):
        LinearStore(client).create_issue(UUID, "ACME", "T", "b", None, ["deckhand", "ghost"])
    assert "IssueCreate" not in client.names


def test_issue_maps_the_node() -> None:
    client = FakeClient(Issue={"issue": ISSUE_NODE})
    assert LinearStore(client).issue(UUID) == {
        "id": UUID,
        "team": "ACME",
        "number": 5,
        "identifier": "ACME-5",
        "title": "Title",
        "description": "body",
        "project": "proj-1",
        "labels": ["deckhand"],
    }
    assert client.calls == [("Issue", {"id": UUID})]


def test_issue_without_project_maps_none() -> None:
    client = FakeClient(Issue={"issue": {**ISSUE_NODE, "project": None}})
    assert LinearStore(client).issue(UUID)["project"] is None


def test_issue_not_found_is_none() -> None:
    client = FakeClient(Issue=LinearError("INPUT_ERROR", "Entity not found: Issue"))
    assert LinearStore(client).issue(UUID) is None


def test_issue_other_errors_propagate() -> None:
    client = FakeClient(Issue=LinearError("network", "could not reach Linear"))
    with pytest.raises(LinearError):
        LinearStore(client).issue(UUID)


def test_issue_by_ref_queries_by_identifier() -> None:
    client = FakeClient(Issue={"issue": ISSUE_NODE})
    found = LinearStore(client).issue_by_ref(Ref("ACME", 5))
    assert found is not None and found["id"] == UUID
    assert client.calls == [("Issue", {"id": "ACME-5"})]


def test_set_labels_resolves_ids_in_the_issue_team() -> None:
    client = FakeClient(
        Issue={"issue": ISSUE_NODE},
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        LabelIds={"issueLabels": {"nodes": [{"id": "l2", "name": "ready"}]}},
        IssueUpdate={"issueUpdate": {"success": True}},
    )
    LinearStore(client).set_labels(UUID, ["ready"])
    assert client.calls == [
        ("Issue", {"id": UUID}),
        ("TeamId", {"key": "ACME"}),
        ("LabelIds", {"names": ["ready"], "teamId": TEAM_ID}),
        ("IssueUpdate", {"id": UUID, "input": {"labelIds": ["l2"]}}),
    ]


def test_workspace_label_survives_repository_swap() -> None:
    workspace_label = {"id": "w1", "name": "Urgent"}
    client = FakeClient(
        Issue={"issue": ISSUE_NODE},
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        LabelIds={"issueLabels": {"nodes": [{"id": "l2", "name": "gadgets"}, workspace_label]}},
        IssueUpdate={"issueUpdate": {"success": True}},
    )
    LinearStore(client).set_labels(UUID, ["gadgets", "Urgent"])
    assert client.calls[-1] == ("IssueUpdate", {"id": UUID, "input": {"labelIds": ["l2", "w1"]}})
    assert "null: true" in client.documents[2]


def test_set_labels_empty_clears_without_lookup() -> None:
    client = FakeClient(IssueUpdate={"issueUpdate": {"success": True}})
    LinearStore(client).set_labels(UUID, [])
    assert client.calls == [("IssueUpdate", {"id": UUID, "input": {"labelIds": []}})]


def test_put_document_on_an_issue_creates() -> None:
    client = FakeClient(
        Issue={"issue": ISSUE_NODE},
        DocumentCreate={"documentCreate": {"success": True, "document": {"id": "d1"}}},
    )
    LinearStore(client).put_document("d1", UUID, "Plan", "# Plan")
    assert client.calls == [
        ("Issue", {"id": UUID}),
        ("DocumentCreate", {"input": {"id": "d1", "issueId": UUID, "title": "Plan", "content": "# Plan"}}),
    ]


def test_put_document_on_a_project_creates() -> None:
    client = FakeClient(
        Issue=LinearError("INPUT_ERROR", "Entity not found: Issue"),
        DocumentCreate={"documentCreate": {"success": True, "document": {"id": "d1"}}},
    )
    LinearStore(client).put_document("d1", "proj-1", "Plan", "# Plan")
    assert client.calls[1] == (
        "DocumentCreate",
        {"input": {"id": "d1", "projectId": "proj-1", "title": "Plan", "content": "# Plan"}},
    )


def test_put_document_repeat_id_updates() -> None:
    client = FakeClient(
        Issue={"issue": ISSUE_NODE},
        DocumentCreate=duplicate("Document"),
        DocumentUpdate={"documentUpdate": {"success": True}},
    )
    LinearStore(client).put_document("d1", UUID, "Plan", "# New")
    assert client.calls[-1] == ("DocumentUpdate", {"id": "d1", "input": {"title": "Plan", "content": "# New"}})


def test_put_document_other_duplicate_entity_propagates() -> None:
    client = FakeClient(Issue={"issue": ISSUE_NODE}, DocumentCreate=duplicate("Issue"))
    with pytest.raises(DuplicateId):
        LinearStore(client).put_document("d1", UUID, "Plan", "x")


def test_document_maps_the_node() -> None:
    node = {"id": "d1", "title": "Plan", "content": "# Plan", "issue": {"id": UUID}, "project": None}
    client = FakeClient(Document={"document": node})
    assert LinearStore(client).document("d1") == {
        "id": "d1",
        "parent": UUID,
        "title": "Plan",
        "body": "# Plan",
    }
    assert client.calls == [("Document", {"id": "d1"})]


def test_document_of_a_project_names_the_project() -> None:
    node = {"id": "d1", "title": "Plan", "content": "x", "issue": None, "project": {"id": "proj-1"}}
    client = FakeClient(Document={"document": node})
    assert LinearStore(client).document("d1")["parent"] == "proj-1"


def test_document_not_found_is_none() -> None:
    client = FakeClient(Document=LinearError("INPUT_ERROR", "Entity not found: Document"))
    assert LinearStore(client).document("d1") is None


def test_put_record_uses_fixed_url_per_kind_and_subject() -> None:
    client = FakeClient(AttachmentCreate={"attachmentCreate": {"success": True, "attachment": {"id": "a"}}})
    store = LinearStore(client)
    first = record()
    store.put_record(UUID, first)
    store.put_record(UUID, Record("approval", "plan", "v2", "captain", {"ok": False}))
    urls = [variables["input"]["url"] for _, variables in client.calls]
    assert urls == ["https://deckhand.invalid/record/approval/plan"] * 2
    assert client.calls[0] == (
        "AttachmentCreate",
        {
            "input": {
                "issueId": UUID,
                "url": "https://deckhand.invalid/record/approval/plan",
                "title": "deckhand approval",
                "metadata": {"record": first.to_json()},
            }
        },
    )


def test_put_record_distinct_subjects_use_distinct_urls() -> None:
    client = FakeClient(AttachmentCreate={"attachmentCreate": {"success": True, "attachment": {"id": "a"}}})
    store = LinearStore(client)
    store.put_record(UUID, record("plan"))
    store.put_record(UUID, record("review"))
    assert client.calls[0][1]["input"]["url"] != client.calls[1][1]["input"]["url"]


def attachment(rec: Record) -> dict:
    url = f"https://deckhand.invalid/record/{rec.kind}/{rec.subject}"
    return {"url": url, "metadata": {"record": rec.to_json()}}


def test_records_decodes_only_deckhand_urls_sorted_by_url() -> None:
    nodes = [
        attachment(record("review")),
        {"url": "https://github.com/acme/widgets/pull/1", "metadata": {}},
        attachment(record("plan")),
    ]
    client = FakeClient(IssueAttachments={"issue": {"attachments": {"nodes": nodes, "pageInfo": DONE}}})
    assert LinearStore(client).records(UUID) == [record("plan"), record("review")]
    assert client.calls == [("IssueAttachments", {"id": UUID})]


def test_records_refuses_a_malformed_record_naming_issue_and_url() -> None:
    bad = {"url": "https://deckhand.invalid/record/approval/plan", "metadata": {"record": "{nope"}}
    client = FakeClient(IssueAttachments={"issue": {"attachments": {"nodes": [bad], "pageInfo": DONE}}})
    with pytest.raises(Refusal, match=rf"{UUID}.*approval/plan"):
        LinearStore(client).records(UUID)


def test_records_refuses_metadata_without_a_record() -> None:
    bad = {"url": "https://deckhand.invalid/record/approval/plan", "metadata": {}}
    client = FakeClient(IssueAttachments={"issue": {"attachments": {"nodes": [bad], "pageInfo": DONE}}})
    with pytest.raises(Refusal, match="approval/plan"):
        LinearStore(client).records(UUID)


def test_relate_sends_blocker_as_issue_and_blocked_as_related() -> None:
    client = FakeClient(IssueRelationCreate={"issueRelationCreate": {"success": True}})
    LinearStore(client).relate("a", "b")
    assert client.calls == [
        ("IssueRelationCreate", {"input": {"issueId": "a", "relatedIssueId": "b", "type": "blocks"}})
    ]


def test_relations_reads_both_directions_blocks_only_sorted() -> None:
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
    assert LinearStore(client).relations("m") == [("a", "m"), ("m", "z")]
    assert client.calls == [("IssueRelations", {"id": "m"})]


def test_relations_of_a_missing_issue_is_empty() -> None:
    client = FakeClient(IssueRelations=LinearError("INPUT_ERROR", "Entity not found: Issue"))
    assert LinearStore(client).relations("m") == []


def test_records_of_a_missing_issue_is_empty() -> None:
    client = FakeClient(IssueAttachments=LinearError("INPUT_ERROR", "Entity not found: Issue"))
    assert LinearStore(client).records(UUID) == []


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
        ("IssueAttachments", {"issue": {"attachments": {"nodes": [], "pageInfo": MORE}}}, "records"),
        ("IssueRelations", {"issue": relations_node(relations=MORE)}, "relations"),
        ("IssueRelations", {"issue": relations_node(inverse=MORE)}, "relations"),
    ],
)
def test_a_page_overflow_refuses_naming_the_issue(name: str, response: dict, read: str) -> None:
    client = FakeClient(**{name: response})
    with pytest.raises(Refusal, match=rf"{UUID}.*more than {queries.PAGE_SIZE}"):
        getattr(LinearStore(client), read)(UUID)


OTHER = "7c1d2e3f-4a5b-4c6d-8e7f-901a2b3c4d5e"
OTHER_NODE = {**ISSUE_NODE, "id": OTHER, "identifier": "ACME-6", "number": 6}


def test_set_labels_writes_the_uuid_whichever_form_is_passed() -> None:
    for form in (UUID, "ACME-5"):
        client = FakeClient(Issue={"issue": ISSUE_NODE}, IssueUpdate={"issueUpdate": {"success": True}})
        LinearStore(client).set_labels(form, [])
        assert client.calls[-1] == ("IssueUpdate", {"id": UUID, "input": {"labelIds": []}})


def test_put_record_and_relate_write_uuids_for_identifiers() -> None:
    client = FakeClient(
        Issue=[{"issue": ISSUE_NODE}, {"issue": ISSUE_NODE}, {"issue": OTHER_NODE}],
        AttachmentCreate={"attachmentCreate": {"success": True, "attachment": {"id": "a"}}},
        IssueRelationCreate={"issueRelationCreate": {"success": True}},
    )
    store = LinearStore(client)
    store.put_record("ACME-5", record())
    store.relate("ACME-5", "ACME-6")
    writes = [variables["input"] for name, variables in client.calls if name != "Issue"]
    assert writes[0]["issueId"] == UUID
    assert (writes[1]["issueId"], writes[1]["relatedIssueId"]) == (UUID, OTHER)


def test_ensure_relation_writes_nothing_when_held_whichever_form_is_passed() -> None:
    held = {
        "issue": {
            "id": UUID,
            "relations": {"nodes": [{"type": "blocks", "relatedIssue": {"id": OTHER}}], "pageInfo": DONE},
            "inverseRelations": {"nodes": [], "pageInfo": DONE},
        }
    }
    for blocker, blocked in ((UUID, OTHER), ("ACME-5", "ACME-6")):
        client = FakeClient(Issue=[{"issue": ISSUE_NODE}, {"issue": OTHER_NODE}], IssueRelations=held)
        ensure_relation(LinearStore(client), blocker, blocked)
        assert "IssueRelationCreate" not in client.names


def test_an_unknown_identifier_refuses_before_any_write() -> None:
    client = FakeClient(Issue=LinearError("INPUT_ERROR", "Entity not found: Issue"))
    with pytest.raises(Refusal, match="ACME-99"):
        LinearStore(client).relate("ACME-99", UUID)
    assert client.names == ["Issue"]


def test_issue_reads_ask_linear_for_a_full_page() -> None:
    client = FakeClient(
        Issue={"issue": ISSUE_NODE},
        IssueAttachments={"issue": {"attachments": {"nodes": [], "pageInfo": DONE}}},
        IssueRelations={"issue": relations_node()},
    )
    store = LinearStore(client)
    store.issue(UUID)
    store.records(UUID)
    store.relations(UUID)
    for document in client.documents:
        assert f"(first: {queries.PAGE_SIZE})" in document
    assert client.documents[2].count(f"(first: {queries.PAGE_SIZE})") == 2


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


def test_create_project_resolves_the_team_and_sends_teamids() -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        ProjectCreate={"projectCreate": {"success": True, "project": {"id": "proj-1"}}},
    )
    assert LinearStore(client).create_project("proj-1", "ACME", "Feature", "about") == "proj-1"
    assert client.calls == [
        ("TeamId", {"key": "ACME"}),
        (
            "ProjectCreate",
            {"input": {"id": "proj-1", "name": "Feature", "description": "about", "teamIds": [TEAM_ID]}},
        ),
    ]


def test_create_project_sends_project_label_ids() -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        ProjectLabelIds={"projectLabels": {"nodes": [{"id": "pl1", "name": "deckhand"}]}},
        ProjectCreate={"projectCreate": {"success": True, "project": {"id": "proj-1"}}},
    )
    LinearStore(client).create_project("proj-1", "ACME", "Feature", "about", ["deckhand"])
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


def test_set_project_labels_refuses_a_label_the_workspace_lacks() -> None:
    client = FakeClient(ProjectLabelIds={"projectLabels": {"nodes": []}})
    with pytest.raises(Refusal, match="no project label named deckhand"):
        LinearStore(client).set_project_labels("proj-1", ["deckhand"])
    assert client.names == ["ProjectLabelIds"]


def test_set_project_labels_replaces_the_list() -> None:
    client = FakeClient(
        ProjectLabelIds={"projectLabels": {"nodes": [{"id": "pl1", "name": "deckhand"}]}},
        ProjectUpdate={"projectUpdate": {"success": True}},
    )
    LinearStore(client).set_project_labels("proj-1", ["deckhand"])
    assert client.calls[-1] == ("ProjectUpdate", {"id": "proj-1", "input": {"labelIds": ["pl1"]}})


def test_project_past_one_page_of_labels_refuses() -> None:
    node = {**PROJECT_NODE, "labels": {"nodes": [], "pageInfo": MORE}}
    client = FakeClient(Project={"project": node})
    with pytest.raises(Refusal, match="more than 10 project labels"):
        LinearStore(client).project("proj-1")


def test_create_project_repeat_id_raises_duplicate() -> None:
    client = FakeClient(TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}}, ProjectCreate=duplicate("Project"))
    with pytest.raises(DuplicateId) as caught:
        LinearStore(client).create_project("proj-1", "ACME", "Feature", "about")
    assert caught.value.entity == "Project"


def test_project_maps_the_node() -> None:
    client = FakeClient(Project={"project": PROJECT_NODE})
    assert LinearStore(client).project("proj-1") == PROJECT
    assert client.calls == [("Project", {"id": "proj-1"})]


def test_project_not_found_is_none() -> None:
    client = FakeClient(Project=LinearError("INPUT_ERROR", "Entity not found: Project"))
    assert LinearStore(client).project("proj-1") is None


def test_project_other_errors_propagate() -> None:
    client = FakeClient(Project=LinearError("INPUT_ERROR", "bad"))
    with pytest.raises(LinearError):
        LinearStore(client).project("proj-1")


def test_team_projects_queries_the_team_and_maps_nodes() -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        TeamProjects={"team": {"projects": {"nodes": [PROJECT_NODE], "pageInfo": DONE}}},
    )
    assert LinearStore(client).team_projects("ACME") == [PROJECT]
    assert client.calls[1] == ("TeamProjects", {"id": TEAM_ID})
    assert f"(first: {queries.PAGE_SIZE})" in client.documents[1]


def test_team_projects_overflow_refuses() -> None:
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        TeamProjects={"team": {"projects": {"nodes": [], "pageInfo": MORE}}},
    )
    with pytest.raises(Refusal, match=rf"ACME.*more than {queries.PAGE_SIZE}"):
        LinearStore(client).team_projects("ACME")


def test_set_project_dates_sends_both_dates_and_null_clears() -> None:
    client = FakeClient(ProjectUpdate={"projectUpdate": {"success": True}})
    store = LinearStore(client)
    store.set_project_dates("proj-1", "2026-11-01", "2026-12-01")
    store.set_project_dates("proj-1", None, None)
    assert client.calls == [
        ("ProjectUpdate", {"id": "proj-1", "input": {"startDate": "2026-11-01", "targetDate": "2026-12-01"}}),
        ("ProjectUpdate", {"id": "proj-1", "input": {"startDate": None, "targetDate": None}}),
    ]


def test_project_issues_maps_nodes_with_state() -> None:
    node = {
        "id": UUID,
        "identifier": "ACME-5",
        "number": 5,
        "title": "Title",
        "description": "body",
        "team": {"key": "ACME"},
        "state": {"name": "Done"},
        "labels": {"nodes": [{"name": "deckhand"}], "pageInfo": DONE},
    }
    client = FakeClient(ProjectIssues={"project": {"issues": {"nodes": [node], "pageInfo": DONE}}})
    assert LinearStore(client).project_issues("proj-1") == [
        {
            "id": UUID,
            "team": "ACME",
            "number": 5,
            "identifier": "ACME-5",
            "title": "Title",
            "description": "body",
            "project": "proj-1",
            "labels": ["deckhand"],
            "state": "Done",
        }
    ]
    assert client.calls == [("ProjectIssues", {"id": "proj-1"})]
    assert f"(first: {queries.PAGE_SIZE})" in client.documents[0]


def test_project_issues_overflow_refuses() -> None:
    client = FakeClient(ProjectIssues={"project": {"issues": {"nodes": [], "pageInfo": MORE}}})
    with pytest.raises(Refusal, match=rf"proj-1.*more than {queries.PAGE_SIZE}"):
        LinearStore(client).project_issues("proj-1")


def test_project_issues_label_overflow_refuses() -> None:
    node = {
        "id": UUID,
        "identifier": "ACME-5",
        "number": 5,
        "title": "T",
        "description": "",
        "team": {"key": "ACME"},
        "state": {"name": "Done"},
        "labels": {"nodes": [], "pageInfo": MORE},
    }
    client = FakeClient(ProjectIssues={"project": {"issues": {"nodes": [node], "pageInfo": DONE}}})
    with pytest.raises(Refusal, match=rf"{UUID}.*more than {queries.PAGE_SIZE}"):
        LinearStore(client).project_issues("proj-1")


def test_project_issues_of_a_missing_project_is_empty() -> None:
    client = FakeClient(ProjectIssues=LinearError("INPUT_ERROR", "Entity not found: Project"))
    assert LinearStore(client).project_issues("proj-1") == []


def test_set_issue_text_updates_title_and_description() -> None:
    client = FakeClient(IssueUpdate={"issueUpdate": {"success": True}})
    LinearStore(client).set_issue_text(UUID, "New", "Body")
    assert client.calls == [("IssueUpdate", {"id": UUID, "input": {"title": "New", "description": "Body"}})]


def test_repository_labels_lists_the_children_of_the_group() -> None:
    nodes = [
        {"id": "g", "name": "Repository", "isGroup": True, "parent": None},
        {"id": "l1", "name": "widgets", "isGroup": False, "parent": {"name": "Repository"}},
        {"id": "l2", "name": "ready", "isGroup": False, "parent": None},
    ]
    client = FakeClient(
        TeamId={"teams": {"nodes": [{"id": TEAM_ID}]}},
        TeamLabels={"team": {"labels": {"nodes": nodes, "pageInfo": DONE}}},
    )
    assert LinearStore(client).repository_labels("ACME") == ["widgets"]
