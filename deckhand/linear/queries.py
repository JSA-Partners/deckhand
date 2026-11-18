from __future__ import annotations

TEAM_ID = """
query TeamId($key: String!) {
  teams(filter: { key: { eq: $key } }) { nodes { id } }
}
"""

LABEL_IDS = """
query LabelIds($names: [String!]!, $teamId: ID!) {
  issueLabels(filter: { name: { in: $names }, team: { id: { eq: $teamId } } }) {
    nodes { id name }
  }
}
"""

ISSUE = """
query Issue($id: String!) {
  issue(id: $id) {
    id
    identifier
    number
    title
    description
    team { key }
    project { id }
    labels { nodes { id name } }
  }
}
"""

ISSUE_ATTACHMENTS = """
query IssueAttachments($id: String!) {
  issue(id: $id) { attachments { nodes { url metadata } } }
}
"""

ISSUE_RELATIONS = """
query IssueRelations($id: String!) {
  issue(id: $id) {
    id
    relations { nodes { type relatedIssue { id } } }
    inverseRelations { nodes { type issue { id } } }
  }
}
"""

ISSUE_CREATE = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id } }
}
"""

ISSUE_UPDATE = """
mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success }
}
"""

DOCUMENT = """
query Document($id: String!) {
  document(id: $id) { id title content issue { id } project { id } }
}
"""

DOCUMENT_CREATE = """
mutation DocumentCreate($input: DocumentCreateInput!) {
  documentCreate(input: $input) { success document { id } }
}
"""

DOCUMENT_UPDATE = """
mutation DocumentUpdate($id: String!, $input: DocumentUpdateInput!) {
  documentUpdate(id: $id, input: $input) { success }
}
"""

ATTACHMENT_CREATE = """
mutation AttachmentCreate($input: AttachmentCreateInput!) {
  attachmentCreate(input: $input) { success attachment { id } }
}
"""

RELATION_CREATE = """
mutation IssueRelationCreate($input: IssueRelationCreateInput!) {
  issueRelationCreate(input: $input) { success }
}
"""
