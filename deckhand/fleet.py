"""The fleet: every story on the project, the order to build them in, and what does not add up.

The captain asks the project for its items with a query of its own rather than the shared one,
because it needs each issue's comments beside its fields: that way every story's log arrives with
the board in a single paginated read instead of one read per story.
"""

from __future__ import annotations

from dataclasses import dataclass

from deckhand import board, gh, issue
from deckhand.config import Settings

# gh --paginate advances the cursor only when the variable is named endCursor.
ITEMS_QUERY = (
    "query($owner:String!,$number:Int!,$endCursor:String){ OWNER_ROOT(login:$owner){ "
    "projectV2(number:$number){ items(first:50, after:$endCursor){ "
    "pageInfo{ hasNextPage endCursor } nodes{ id "
    "content{ ... on Issue{ number title url state closedAt repository{ nameWithOwner } "
    "comments(last:40){ nodes{ body createdAt author{ login } } } } } "
    "fieldValues(first:20){ nodes{ "
    "... on ProjectV2ItemFieldNumberValue{ number field{ ... on ProjectV2FieldCommon{ name } } } "
    "... on ProjectV2ItemFieldSingleSelectValue{ name field{ ... on ProjectV2FieldCommon{ name } } } "
    "} } } } } } }"
)

DONE = "Done"


@dataclass(frozen=True)
class Story:
    """One story as the board and its own log together describe it."""

    number: int
    repo: str
    title: str
    status: str
    points: int | None
    closed: bool
    item: str
    issue: issue.Issue

    @property
    def key(self) -> tuple[str, int]:
        return (self.repo, self.number)


def _points(node: dict) -> int | None:
    value = board.field_value(node, "Story Points", "number")
    return None if value is None else int(value)


def _issue(content: dict) -> issue.Issue:
    comments = (content.get("comments") or {}).get("nodes") or []
    return issue.Issue(
        number=content["number"],
        title=content.get("title") or "",
        body="",
        url=content.get("url") or "",
        state=content.get("state") or "",
        comments=[
            issue.Comment(
                author=(raw.get("author") or {}).get("login") or "",
                body=raw.get("body") or "",
                created_at=raw.get("createdAt") or "",
            )
            for raw in comments
        ],
    )


def stories(nodes: list[dict]) -> list[Story]:
    """One row per item that is an issue, in the order the project gave them."""
    found = []
    for node in nodes:
        content = node.get("content")
        if not content or "number" not in content:
            continue
        found.append(
            Story(
                number=content["number"],
                repo=(content.get("repository") or {}).get("nameWithOwner") or "",
                title=" ".join((content.get("title") or "").split()),
                status=board.field_value(node, "Status", "name") or "",
                points=_points(node),
                closed=bool(content.get("closedAt")),
                item=node.get("id") or "",
                issue=_issue(content),
            )
        )
    return found


def _nodes(settings: Settings) -> list[dict]:
    query = gh.owner_query(ITEMS_QUERY, settings.owner_type)
    pages = gh.graphql(query, {"owner": settings.owner, "number": settings.project}, paginate=True)
    root = gh.owner_field(settings.owner_type)
    found: list[dict] = []
    for page in pages:
        project = ((page.get("data") or {}).get(root) or {}).get("projectV2") or {}
        found.extend((project.get("items") or {}).get("nodes") or [])
    return found
