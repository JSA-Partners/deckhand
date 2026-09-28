"""The fleet: every story on the project, the order to build them in, and what does not add up.

The captain asks the project for its items with a query of its own rather than the shared one,
because it needs each issue's body and comments beside its fields: that way every story's log arrives with
the board in a single paginated read instead of one read per story.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from deckhand import board, checklist, columns, gh, issue, sections, sessions, step
from deckhand.config import Settings

# gh --paginate advances the cursor only when the variable is named endCursor.
ITEMS_QUERY = (
    "query($owner:String!,$number:Int!,$endCursor:String){ OWNER_ROOT(login:$owner){ "
    "projectV2(number:$number){ items(first:100, after:$endCursor, archivedStates:[ARCHIVED,NOT_ARCHIVED]){ "
    "pageInfo{ hasNextPage endCursor } nodes{ id isArchived "
    "content{ ... on Issue{ number title url state closedAt body repository{ nameWithOwner } "
    "labels(first:20){ nodes{ name } } "
    "assignees(first:10){ nodes{ login } } "
    "blockedBy(first:20){ nodes{ number state title repository{ nameWithOwner } } } "
    "comments(last:40){ nodes{ body createdAt author{ login } } } } } "
    "fieldValues(first:20){ nodes{ "
    "... on ProjectV2ItemFieldNumberValue{ number field{ ... on ProjectV2FieldCommon{ name } } } "
    "... on ProjectV2ItemFieldSingleSelectValue{ name field{ ... on ProjectV2FieldCommon{ name } } } "
    "} } } } } } }"
)

# An open session idle longer than this is not counted as working on its story.
ACTIVE = 3600.0


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


def load(settings: Settings) -> list[Story]:
    """Every story on the project with its log, and nothing else read; an archived one is not a story here.

    The query asks for archived items because `read` reports the archived story the process still owns.
    Nothing else wants them, so this drops them and every caller sees the board as a person does.
    """
    return [story for story in stories(_nodes(settings)) if not story.archived]


def touched(story: Story) -> bool:
    """Whether this issue is the process's business, which is what the label it carries says.

    A board holds issues older than the process and issues opened by hand. Nothing deckhand knows
    applies to them: their column was chosen by a person, and reading it as one of ours would call
    every one of them broken.
    """
    return checklist.LABEL[0] in story.issue.labels


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
    return "reviewed, not boarded" if columns.at_least(story.status, columns.READY) else "review not run"


Key = tuple[str, int]
Blockers = dict[Key, list[tuple[str, int, str]]]


def _circle(start: Key, blockers: Blockers) -> list[Key]:
    """The stories on a loop from `start` back to itself through its blockers, or empty."""
    stack: list[tuple[Key, list[Key]]] = [(start, [start])]
    seen: set[Key] = set()
    while stack:
        node, path = stack.pop()
        for where, number, _ in blockers.get(node) or []:
            if (where, number) == start:
                return [*path, start]
            if (where, number) not in seen:
                seen.add((where, number))
                stack.append(((where, number), [*path, (where, number)]))
    return []


@dataclass(frozen=True)
class Anomaly:
    """Something on the board that no step could have produced, and what puts it right."""

    number: int
    repo: str
    what: str
    fix: str


def anomalies(
    found: list[Story],
    blockers: Blockers,
    behind: set[Key],
    pulses: list,
    missing: list[tuple[str, int, str]] | None = None,
    me: str = "",
    archived: list[Story] | None = None,
) -> list[Anomaly]:
    """Every disagreement worth a line: a wrong Status, a story off the board or archived, a stall, a cycle."""
    on: dict[str, list[str]] = {}
    for beat in pulses:
        if beat.story != sessions.FREE and beat.idle < ACTIVE:
            on.setdefault(beat.story, []).append(beat.label)
    out: list[Anomaly] = []
    for story in found:
        if not touched(story):
            continue
        # Two typed sources, so they can still disagree: only finished work closes an issue, and
        # only a closed story reaches the last two columns.
        if story.status == columns.DONE and not story.closed:
            out.append(Anomaly(story.number, story.repo, "Done, but the issue is open", "close it or move it back"))
        if story.closed and story.status and story.status not in (columns.DONE, columns.VERIFICATION):
            what = f"closed, but {story.status}"
            out.append(Anomaly(story.number, story.repo, what, f"Status {columns.VERIFICATION} or {columns.DONE}"))
        labels = on.get(str(story.number)) or []
        mine = not me or not story.assignees or me in story.assignees
        if story.status == columns.IN_PROGRESS and not labels and mine and not blockers.get(story.key):
            out.append(Anomaly(story.number, story.repo, "In Progress, no session open", "none"))
        if len(labels) > 1:
            named = " and ".join(labels)
            out.append(Anomaly(story.number, story.repo, f"sessions {named} are both on it", "close one"))
        if story.key in behind:
            out.append(Anomaly(story.number, story.repo, "pull request behind main", f"run next {story.number}"))
        loop = _circle(story.key, blockers)
        if loop:
            named = " -> ".join(step.ref_label(where, number, story.repo) for where, number in loop)
            fix = "captain apply --unblock on one edge"
            out.append(Anomaly(story.number, story.repo, f"blockers run in a circle: {named}", fix))
    for repo, number, _ in missing or []:
        out.append(Anomaly(number, repo, "drafted, but not on the board", "add it"))
    for story in archived or []:
        # Archiving is how finished work leaves the board, so only a story still in play is wrong.
        if not touched(story) or story.status == columns.DONE:
            continue
        what = f"archived, and still {story.status or 'without a column'}"
        out.append(Anomaly(story.number, story.repo, what, "unarchive it on the board"))
    return out


@dataclass(frozen=True)
class Fleet:
    """One reading of the project: its stories, its Backlog's blockers, what is behind main, what is off it."""

    stories: list[Story]
    blockers: Blockers
    behind: set[Key]
    missing: list[tuple[str, int, str]]
    me: str = ""
    archived: list[Story] = field(default_factory=list)


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
            if checklist.LABEL[0] in story.labels:
                off.append((repo, number, story.url))
    return off


def read(settings: Settings) -> Fleet:
    """The whole fleet: one query with every unfinished story's blockers, a merge state per open pull request.

    Archived items are read so an archived story can be named, and held apart from the rest, because
    the project archives finished work by itself and those closed items would bury the stories.
    """
    every = stories(_nodes(settings))
    found = [story for story in every if not story.archived]
    blockers: Blockers = {
        story.key: list(story.blocked_by) for story in found if not story.closed and story.status != columns.DONE
    }
    behind: set[Key] = set()
    for story in found:
        if story.status not in (columns.IN_PROGRESS, columns.IN_REVIEW):
            continue
        try:
            found_pr = issue.pull_request_for(story.repo, story.number)
        except gh.GhError:
            continue  # a pull request gh cannot read says nothing about main; the row stands without it
        if found_pr is not None and found_pr.behind:
            behind.add(story.key)
    return Fleet(
        stories=found,
        blockers=blockers,
        behind=behind,
        missing=_missing(every),  # archived included: an archived story is on the board, not off it
        me=gh.login(),
        archived=[story for story in every if story.archived],
    )
