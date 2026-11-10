"""The fleet: every story on the project, the order to build them in, and what does not add up.

The captain asks the project for its items with a query of its own rather than the shared one,
because it needs each issue's body and comments beside its fields: that way every story's log arrives with
the board in a single paginated read instead of one read per story.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from deckhand import board, columns, draft, gh, issue, log, sections, step
from deckhand.config import Settings

# gh --paginate advances the cursor only when the variable is named endCursor.
ITEMS_QUERY = (
    "query($owner:String!,$number:Int!,$endCursor:String){ OWNER_ROOT(login:$owner){ "
    "projectV2(number:$number){ items(first:100, after:$endCursor, archivedStates:[ARCHIVED,NOT_ARCHIVED]){ "
    "pageInfo{ hasNextPage endCursor } nodes{ id isArchived "
    "content{ ... on Issue{ number title url state stateReason closedAt body repository{ nameWithOwner } "
    "labels(first:20){ nodes{ name } } "
    "assignees(first:10){ nodes{ login } } "
    "blockedBy(first:20){ nodes{ number state title repository{ nameWithOwner } } } "
    "comments(last:100){ nodes{ body createdAt author{ login } } } } } "
    "fieldValues(first:30){ nodes{ "
    "... on ProjectV2ItemFieldNumberValue{ number field{ ... on ProjectV2FieldCommon{ name } } } "
    "... on ProjectV2ItemFieldSingleSelectValue{ name optionId field{ ... on ProjectV2FieldCommon{ name } } } "
    "} } } } } } }"
)


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
    assignees: tuple[str, ...] = ()
    blocked_by: tuple[tuple[str, int, str], ...] = ()
    archived: bool = False
    feature: str | None = None
    feature_name: str = ""
    dropped: bool = False
    closed_at: str = ""

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
        body=content.get("body") or "",
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
        labels=tuple(str(node.get("name") or "") for node in (content.get("labels") or {}).get("nodes") or []),
    )


def _open_blockers(content: dict) -> tuple[tuple[str, int, str], ...]:
    """`(repository, number, title)` of every open issue blocking this one, as the query returned it."""
    return tuple(
        (
            (node.get("repository") or {}).get("nameWithOwner") or "",
            int(node["number"]),
            " ".join((node.get("title") or "").split()),
        )
        for node in (content.get("blockedBy") or {}).get("nodes") or []
        if node.get("state") == "OPEN" and "number" in node
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
                assignees=tuple(
                    str(person.get("login") or "") for person in (content.get("assignees") or {}).get("nodes") or []
                ),
                blocked_by=_open_blockers(content),
                archived=bool(node.get("isArchived")),
                feature=board.field_value(node, board.FEATURE, "optionId"),
                feature_name=board.field_value(node, board.FEATURE, "name") or "",
                dropped=content.get("stateReason") in issue.DROPPED,
                closed_at=content.get("closedAt") or "",
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


def load(settings: Settings, archived: bool = False) -> list[Story]:
    """Every story on the project with its log, and nothing else read; an archived one only when asked for.

    The query asks for archived items because `read` reports the archived story the process still owns.
    Most callers want the board as a person sees it, so archived items are dropped unless asked for.
    """
    return [story for story in stories(_nodes(settings)) if archived or not story.archived]


def touched(story: Story) -> bool:
    """Whether this issue is the process's business, which is what the label it carries says.

    A board holds issues older than the process and issues opened by hand. Nothing deckhand knows
    applies to them: their column was chosen by a person, and reading it as one of ours would call
    every one of them broken.
    """
    return issue.LABEL[0] in story.issue.labels


def members(found: list[Story], feature: str) -> list[Story]:
    """The stories the process owns whose Feature is the option `feature`, in any repository, none dropped."""
    return [story for story in found if story.feature == feature and touched(story) and not story.dropped]


SHED = re.compile(r"(?:([^\s/#,]+/[^\s/#,]+))?#([0-9]+)")


def shed(story: Story) -> list[tuple[str, int]]:
    """`(repository, number)` of every story this one was split into, as its `Split:` entries name them."""
    found = []
    for entry in log.entries(story.issue):
        if entry.prefix != "Split:":
            continue
        # A split's title is free text and may name an issue, so only the references the process wrote are read.
        if entry.text.startswith("into "):
            named = entry.text.removeprefix("into ").split(", ")
        else:
            named = entry.text.split()[:1]
        for token in named:
            match = SHED.fullmatch(token)
            if match is not None:
                found.append(folded((match.group(1) or story.repo, int(match.group(2)))))
    return found


def note(story: Story, blockers: list[tuple[str, int, str]], behind: bool) -> str:
    """The one thing worth saying about this story beyond its column."""
    if not touched(story):
        return "not a deckhand story"
    if blockers:
        # a story already built, rebased and reviewed still waits on an unmerged blocker; the column alone hides it
        named = ", ".join(step.ref_label(where, number, story.repo) for where, number, _ in blockers)
        return f"waits on {named}"
    if story.status in (columns.IN_PROGRESS, columns.IN_REVIEW):
        if behind:
            return "pull request behind main"
        return "pull request open" if story.status == columns.IN_REVIEW else "building"
    if story.status == columns.BACKLOG:
        return "ready"
    if story.status == columns.VERIFICATION:
        left = sum(1 for _, ticked in sections.after_merge_items(story.issue.body) if not ticked)
        items = "item" if left == 1 else "items"
        return f"merged, {left} {items} left"
    if story.status == columns.DONE:
        return "done" if story.closed else "Done, but the issue is open"
    if columns.at_least(story.status, columns.READY):
        return "reviewed, not boarded"
    if draft.is_draft(story.issue.body):
        return "draft to write" if draft.read(story.issue.body)[1] else "draft to settle"
    return "review not run"


Key = tuple[str, int]


def folded(key: Key) -> Key:
    """The key as GitHub matches one: the repository part ignores case, as a person's typing may not."""
    return key[0].lower(), key[1]


Blockers = dict[Key, list[tuple[str, int, str]]]


@dataclass(frozen=True)
class Fleet:
    """One reading of the project: its stories, its Backlog's blockers, its open pull requests, what is off it."""

    stories: list[Story]
    blockers: Blockers
    missing: list[tuple[str, int, str]]
    me: str = ""
    archived: list[Story] = field(default_factory=list)
    pulls: dict[Key, issue.PullRequest] = field(default_factory=dict)
    features: tuple[str, ...] = ()  # the Feature option ids in the pipeline order the person set

    @property
    def behind(self) -> set[Key]:
        return {key for key, pull in self.pulls.items() if pull.behind}


def _missing(on_board: list[Story]) -> list[tuple[str, int, str]]:
    """`(repo, number, url)` of every open story the process owns that never reached the board.

    One cheap list per repository, and a read of an issue only when the board does not already hold
    it, so the usual answer of none costs one call per repository and nothing else. An issue without
    the label is not the process's business and is never reported.
    """
    keys = {story.key for story in on_board}
    off: list[tuple[str, int, str]] = []
    for repo in sorted({story.repo for story in on_board if story.repo}):
        for number in issue.list_open(repo):
            if (repo, number) in keys:
                continue
            story = issue.view(repo, number)
            if issue.LABEL[0] in story.labels:
                off.append((repo, number, story.url))
    return off


def _blockers(found: list[Story]) -> Blockers:
    """The open blockers of every story still to finish, which is what the order reads."""
    return {story.key: list(story.blocked_by) for story in found if not story.closed and story.status != columns.DONE}


def read(settings: Settings) -> Fleet:
    """The whole fleet: one query with every unfinished story's blockers, every open pull request it links to.

    Archived items are read so an archived story can be named, and held apart from the rest, because
    the project archives finished work by itself and those closed items would bury the stories.
    """
    every = stories(_nodes(settings))
    found = [story for story in every if not story.archived]
    blockers = _blockers(found)
    pulls: dict[Key, issue.PullRequest] = {}
    for story in found:
        if story.status not in (columns.IN_PROGRESS, columns.IN_REVIEW):
            continue
        try:
            found_pr = issue.pull_request_for(story.repo, story.number)
        except gh.GhError:
            continue  # a pull request gh cannot read says nothing about main; the row stands without it
        if found_pr is not None and found_pr.state == "OPEN":
            pulls[story.key] = found_pr
    return Fleet(
        stories=found,
        blockers=blockers,
        pulls=pulls,
        missing=_missing(every),  # archived included: an archived story is on the board, not off it
        me=gh.login(),
        archived=[story for story in every if story.archived],
        features=_pipeline(settings),
    )


def read_order(settings: Settings) -> Fleet:
    """The stories, their blockers and the feature order, which is all the build order reads."""
    found = load(settings)
    blockers = _blockers(found)
    return Fleet(stories=found, blockers=blockers, missing=[], features=_pipeline(settings))


def _pipeline(settings: Settings) -> tuple[str, ...]:
    """The Feature option ids in order, or empty when there is no such field or it cannot be read."""
    try:
        found = next((item for item in gh.project_fields(settings) if item["name"] == board.FEATURE), None)
    except gh.GhError:
        return ()
    return tuple(str(option["id"]) for option in (found or {}).get("options") or [])
