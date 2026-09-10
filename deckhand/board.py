"""The project board: adding an issue, reading its items, the oldest open story of a repository,
and reading what a view shows.

The board holds issues from every repository linked to the project, so a read that means one
repository filters the items by their repository's `nameWithOwner`.
"""

from __future__ import annotations

from typing import Any

from deckhand import gh
from deckhand.config import Settings

# gh --paginate advances the cursor only when the variable is named endCursor.
ITEMS_QUERY = (
    "query($owner:String!,$number:Int!,$endCursor:String){ OWNER_ROOT(login:$owner){ "
    "projectV2(number:$number){ items(first:100, after:$endCursor){ "
    "pageInfo{ hasNextPage endCursor } nodes{ "
    "content{ ... on Issue{ number closedAt title body repository{ nameWithOwner } } } "
    "fieldValues(first:30){ nodes{ "
    "... on ProjectV2ItemFieldNumberValue{ number field{ ... on ProjectV2FieldCommon{ name } } } "
    "... on ProjectV2ItemFieldSingleSelectValue{ name field{ ... on ProjectV2FieldCommon{ name } } } "
    "} } } } } } }"
)

VIEWS_QUERY = (
    "query($owner:String!,$number:Int!){ OWNER_ROOT(login:$owner){ projectV2(number:$number){ "
    "views(first:20){ nodes{ name fields(first:50){ nodes{ ... on ProjectV2FieldCommon{ name } } } } } } } }"
)


def add(settings: Settings, url: str) -> None:
    """Put the issue at `url` on the project; GitHub takes an issue already there as a no-op."""
    gh.run("project", "item-add", str(settings.project), "--owner", settings.owner, "--url", url, "--format", "json")


def items(settings: Settings) -> list[dict[str, Any]]:
    """Every item node on the project, across every page."""
    query = gh.owner_query(ITEMS_QUERY, settings.owner_type)
    pages = gh.graphql(query, {"owner": settings.owner, "number": settings.project}, paginate=True)
    root = gh.owner_field(settings.owner_type)
    found: list[dict[str, Any]] = []
    for page in pages:
        project = ((page.get("data") or {}).get(root) or {}).get("projectV2") or {}
        found.extend((project.get("items") or {}).get("nodes") or [])
    return found


def field_value(node: dict[str, Any], name: str, key: str) -> Any:
    """One field's `key` on an item node, or None when the item has no value for it."""
    for value in (node.get("fieldValues") or {}).get("nodes") or []:
        if (value.get("field") or {}).get("name") == name:
            return value.get(key)
    return None


def oldest_open(settings: Settings, repo: str, exclude: int) -> tuple[int, str] | None:
    """`(number, title)` of the lowest-numbered open story of `repo` on the board that is not Done.

    None when there is no such story; an item with no Status counts as open.
    """
    candidates: list[tuple[int, str]] = []
    for node in items(settings):
        content = node.get("content") or {}
        number = content.get("number")
        if number is None or number == exclude or content.get("closedAt"):
            continue
        if (content.get("repository") or {}).get("nameWithOwner") != repo:
            continue
        if field_value(node, "Status", "name") == "Done":
            continue
        candidates.append((number, " ".join((content.get("title") or "").split())))
    return min(candidates) if candidates else None


def view_fields(settings: Settings) -> dict[str, list[str]]:
    """The fields each view of the project shows, by view name, in the view's own order."""
    query = gh.owner_query(VIEWS_QUERY, settings.owner_type)
    data = gh.graphql(query, {"owner": settings.owner, "number": settings.project})[0]
    root = (data.get("data") or {}).get(gh.owner_field(settings.owner_type)) or {}
    views = ((root.get("projectV2") or {}).get("views") or {}).get("nodes") or []
    return {
        view.get("name") or "": [f.get("name") or "" for f in ((view.get("fields") or {}).get("nodes") or [])]
        for view in views
    }
