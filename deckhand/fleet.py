"""The fleet: every story on the project, the order to build them in, and what does not add up.

The captain asks the project for its items with a query of its own rather than the shared one,
because it needs each issue's comments beside its fields: that way every story's log arrives with
the board in a single paginated read instead of one read per story.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from deckhand import board, gh, issue, log, step
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


def allowed(story: Story) -> tuple[str, ...]:
    """The board Statuses this story's log allows, canonical one first.

    Every status is written by a step's apply, so a board holding anything else was moved by
    something that was not a step. Boarding is the one place a person's answer sits between two
    statuses: a reviewed story is Backlog once the question has been answered and Draft until then.
    """
    if story.closed:
        return (DONE,)
    if log.last(story.issue, "Started:") is not None:
        return ("In Progress",)
    if log.last(story.issue, "Review:") is not None:
        return ("Backlog", "Draft")
    return ("Draft",)


def note(story: Story, blockers: list[tuple[str, int, str]], behind: bool) -> str:
    """The one thing worth saying about this story beyond its column."""
    if story.status == "In Progress":
        if behind:
            return "pull request behind main"
        if log.last(story.issue, "Pull request:") is not None:
            return "pull request open"
        if log.last(story.issue, "Reviewed:") is not None:
            return "reviewed, no pull request"
        return "building"
    if blockers:
        named = ", ".join(step.ref_label(where, number, story.repo) for where, number, _ in blockers)
        return f"waits on {named}"
    if story.status == "Backlog":
        return "ready"
    if story.status == DONE and story.closed:
        return "done"
    return "reviewed, not boarded" if log.last(story.issue, "Review:") is not None else "review not run"


Key = tuple[str, int]
Blockers = dict[Key, list[tuple[str, int, str]]]


@dataclass(frozen=True)
class Ranked:
    """One story in the build order, with the reason it sits where it does."""

    story: Story
    why: str


def _downstream(start: Key, waiting: dict[Key, set[Key]]) -> set[Key]:
    """Every story that transitively waits on `start`; a cycle counts each member once and stops."""
    found: set[Key] = set()
    stack = list(waiting.get(start, ()))
    while stack:
        node = stack.pop()
        if node in found:
            continue
        found.add(node)
        stack.extend(waiting.get(node, ()))
    found.discard(start)
    return found


def _waiting(blockers: Blockers) -> dict[Key, set[Key]]:
    """The blockers map turned around: who is waiting on each story."""
    found: dict[Key, set[Key]] = {}
    for key, holds in blockers.items():
        for where, number, _ in holds:
            found.setdefault((where, number), set()).add(key)
    return found


def _topological(held: list[Story], blockers: Blockers, rank: Callable[[Story], tuple[int, int, int]]) -> list[Story]:
    """`held` placed so nothing precedes a blocker that is also in `held`; a cycle falls back to rank."""
    remaining = list(held)
    left = {story.key for story in remaining}
    placed: list[Story] = []
    while remaining:
        free = [s for s in remaining if not {(w, n) for w, n, _ in blockers.get(s.key) or []} & left]
        best = min(free or remaining, key=rank)
        placed.append(best)
        remaining.remove(best)
        left.discard(best.key)
    return placed


def _why(story: Story, blockers: list[tuple[str, int, str]], below: set[Key], points: dict[Key, int]) -> str:
    if blockers:
        named = ", ".join(step.ref_label(where, number, story.repo) for where, number, _ in blockers)
        return f"waits on {named}"
    if not below:
        return "ready, unblocks nothing"
    count = len(below)
    stories = "story" if count == 1 else "stories"
    return f"ready, unblocks {count} {stories}, {sum(points.get(key, 0) for key in below)} pts"


def order(backlog: list[Story], blockers: Blockers) -> list[Ranked]:
    """The Backlog ranked: what can start first, by the work it frees, then what waits.

    Weight is the points of everything transitively waiting on a story rather than a count of its
    neighbours, so the story that frees the longest chain leads. Equal weight breaks toward fewer
    points, so a cheap unblocker goes first, and equal again breaks by number so two runs agree.
    """
    waiting = _waiting(blockers)
    points = {story.key: story.points or 0 for story in backlog}
    ready = [story for story in backlog if not blockers.get(story.key)]
    held = [story for story in backlog if blockers.get(story.key)]
    weights = {story.key: _downstream(story.key, waiting) for story in backlog}

    def rank(story: Story) -> tuple[int, int, int]:
        below = weights[story.key]
        return (-sum(points.get(key, 0) for key in below), story.points or 0, story.number)

    placed = sorted(ready, key=rank) + _topological(held, blockers, rank)
    return [
        Ranked(story=story, why=_why(story, blockers.get(story.key) or [], weights[story.key], points))
        for story in placed
    ]
