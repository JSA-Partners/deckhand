from __future__ import annotations

PAGE_SIZE = 250
PAGE = f"(first: {PAGE_SIZE})"
MORE = "pageInfo { hasNextPage }"

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

ISSUE = f"""
query Issue($id: String!) {{
  issue(id: $id) {{
    id
    identifier
    number
    title
    description
    team {{ key }}
    project {{ id }}
    labels{PAGE} {{ nodes {{ id name }} {MORE} }}
  }}
}}
"""

ISSUE_ATTACHMENTS = f"""
query IssueAttachments($id: String!) {{
  issue(id: $id) {{ attachments{PAGE} {{ nodes {{ url metadata }} {MORE} }} }}
}}
"""

ISSUE_RELATIONS = f"""
query IssueRelations($id: String!) {{
  issue(id: $id) {{
    id
    relations{PAGE} {{ nodes {{ type relatedIssue {{ id }} }} {MORE} }}
    inverseRelations{PAGE} {{ nodes {{ type issue {{ id }} }} {MORE} }}
  }}
}}
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

TEAM_BY_KEY = """
query TeamByKey($key: String!) {
  teams(filter: { key: { eq: $key } }) {
    nodes { id key autoArchivePeriod autoCloseParentIssues }
  }
}
"""

TEAM_STATES = f"""
query TeamStates($id: String!) {{
  team(id: $id) {{
    states{PAGE} {{ nodes {{ id name type }} {MORE} }}
  }}
}}
"""

TEAM_LABELS = f"""
query TeamLabels($id: String!) {{
  team(id: $id) {{
    labels{PAGE} {{ nodes {{ id name isGroup parent {{ name }} }} {MORE} }}
  }}
}}
"""

TEAM_AUTOMATION = f"""
query TeamAutomation($id: String!) {{
  team(id: $id) {{
    gitAutomationStates{PAGE} {{ nodes {{ id event state {{ name }} }} {MORE} }}
  }}
}}
"""

WORKFLOW_STATE_CREATE = """
mutation WorkflowStateCreate($input: WorkflowStateCreateInput!) {
  workflowStateCreate(input: $input) { success }
}
"""

GIT_AUTOMATION_STATE_CREATE = """
mutation GitAutomationStateCreate($input: GitAutomationStateCreateInput!) {
  gitAutomationStateCreate(input: $input) { success }
}
"""

GIT_AUTOMATION_STATE_UPDATE = """
mutation GitAutomationStateUpdate($id: String!, $input: GitAutomationStateUpdateInput!) {
  gitAutomationStateUpdate(id: $id, input: $input) { success }
}
"""

ISSUE_LABEL_CREATE = """
mutation IssueLabelCreate($input: IssueLabelCreateInput!) {
  issueLabelCreate(input: $input) { success }
}
"""

TEAM_UPDATE = """
mutation TeamUpdate($id: String!, $input: TeamUpdateInput!) {
  teamUpdate(id: $id, input: $input) { success }
}
"""
