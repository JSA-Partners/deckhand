"""Every read and write of a GitHub issue: the body, its comments, and its blocking dependencies.

Bodies always travel through a temp file, so quoting never matters and a body of any size works.
"""

from __future__ import annotations

import functools
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from deckhand import gh

VIEW_FIELDS = "number,title,body,url,state,comments,labels"
# The board cannot say an issue is deckhand's, because it carries issues that are not; this label does.
LABEL = ("deckhand", "5319e7", "A story deckhand runs")

_ISSUE_URL = re.compile(r"https://\S+/issues/([0-9]+)(?:#\S+)?")


@dataclass(frozen=True)
class Comment:
    """One issue comment."""

    author: str
    body: str
    created_at: str


@dataclass(frozen=True)
class Issue:
    """An issue as the process reads it: the body every step edits and every comment on it."""

    number: int
    title: str
    body: str
    url: str
    state: str
    comments: list[Comment]
    labels: tuple[str, ...] = ()


@contextmanager
def _body_file(body: str) -> Iterator[str]:
    """Yield the path of a temp file holding `body`; it is deleted when the caller is done."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", encoding="utf-8", newline="\n", delete_on_close=False
    ) as handle:
        handle.write(body)
        handle.close()
        yield handle.name


def _parsed_url(output: str, what: str) -> tuple[int, str]:
    """The `(number, url)` gh printed for a created issue or a new comment."""
    url = output.strip()
    match = _ISSUE_URL.fullmatch(url)
    if match is None:
        raise gh.GhError(f"unexpected output from gh {what}: {url}")
    return int(match.group(1)), url


def _comment(raw: dict[str, Any]) -> Comment:
    return Comment(
        author=(raw.get("author") or {}).get("login") or "",
        body=raw.get("body") or "",
        created_at=raw.get("createdAt") or "",
    )


def view(repo: str, number: int) -> Issue:
    """The issue and its comments, oldest comment first."""
    gh.split_repo(repo)
    data = gh.json_out("issue", "view", str(number), "--repo", repo, "--json", VIEW_FIELDS)
    return Issue(
        number=data["number"],
        title=data.get("title") or "",
        body=data.get("body") or "",
        url=data.get("url") or "",
        state=data.get("state") or "",
        comments=[_comment(raw) for raw in data.get("comments") or []],
        labels=tuple(str(raw.get("name") or "") for raw in data.get("labels") or []),
    )


def sibling(repo: str, number: int) -> tuple[str, str]:
    """Another issue's `(state, body)`; all a caller needs to say where that issue has got to."""
    gh.split_repo(repo)
    data = gh.json_out("issue", "view", str(number), "--repo", repo, "--json", "state,body")
    return (data.get("state") or "", data.get("body") or "")


def list_open(repo: str) -> list[int]:
    """The number of every open issue of `repo`, which is where a story that never boarded is found."""
    gh.split_repo(repo)
    data = gh.json_out("issue", "list", "--repo", repo, "--state", "open", "--limit", "200", "--json", "number")
    return [item["number"] for item in data or [] if isinstance(item, dict) and "number" in item]


def create(repo: str, title: str, body: str) -> tuple[int, str]:
    """Create an issue carrying the marker label; returns its `(number, url)`."""
    gh.split_repo(repo)
    ensure_label(repo)
    with _body_file(body) as path:
        output = gh.run("issue", "create", "--repo", repo, "--title", title, "--body-file", path, "--label", LABEL[0])
    return _parsed_url(output, "issue create")


@functools.cache
def ensure_label(repo: str, known: tuple[str, ...] | None = None) -> bool:
    """Create the marker label in `repo` unless it is there, `known` being the names already read; True when created."""
    name, color, description = LABEL
    if name in (gh.labels(repo) if known is None else known):
        return False
    gh.create_label(repo, name, color, description)
    return True


def update_body(repo: str, number: int, body: str) -> None:
    """Replace the issue body."""
    gh.split_repo(repo)
    with _body_file(body) as path:
        gh.run("issue", "edit", str(number), "--repo", repo, "--body-file", path)


def set_title(repo: str, number: int, title: str) -> None:
    """Replace the issue title, leaving the body alone."""
    gh.split_repo(repo)
    gh.run("issue", "edit", str(number), "--repo", repo, "--title", title)


def assign(repo: str, number: int, login: str = "@me") -> None:
    """Add `login` to the issue's assignees, leaving the ones already there alone.

    GitHub takes an assignee it already holds as a no-op, so this is safe to run on every start.
    """
    gh.split_repo(repo)
    gh.run("issue", "edit", str(number), "--repo", repo, "--add-assignee", login)


def close(repo: str, number: int) -> None:
    """Close the issue."""
    gh.split_repo(repo)
    gh.run("issue", "close", str(number), "--repo", repo)


def comment(repo: str, number: int, body: str) -> str:
    """Add a comment to the issue; returns its URL."""
    gh.split_repo(repo)
    with _body_file(body) as path:
        output = gh.run("issue", "comment", str(number), "--repo", repo, "--body-file", path)
    return _parsed_url(output, "issue comment")[1]


def add_dependency(repo: str, number: int, blocked_by: tuple[str, int]) -> None:
    """Record that `number` is blocked by `blocked_by`, `(repository, number)`; the API takes the blocker's id.

    The blocker may live in another repository of the organization; its database id is looked up
    there, and the relation is written on the blocked issue. An edge GitHub already holds is not a
    failure: the state the caller asked for is the state there is, and the check costs one read only
    when the write did not land.
    """
    gh.split_repo(repo)
    blocker_repo, blocker = blocked_by
    blocker_id = gh.issue_id(blocker_repo, blocker)
    try:
        gh.run(
            "api",
            "-X",
            "POST",
            f"repos/{repo}/issues/{number}/dependencies/blocked_by",
            "-F",
            f"issue_id={blocker_id}",
        )
    except gh.GhError:
        if blocked_by not in [(where, found) for where, found, _ in blockers(repo, number)]:
            raise


def remove_dependency(repo: str, number: int, blocked_by: tuple[str, int]) -> None:
    """Drop the record that `number` is blocked by `blocked_by`; the API takes the blocker's id."""
    gh.split_repo(repo)
    blocker_repo, blocker = blocked_by
    blocker_id = gh.issue_id(blocker_repo, blocker)
    gh.run("api", "-X", "DELETE", f"repos/{repo}/issues/{number}/dependencies/blocked_by/{blocker_id}")


def blockers(repo: str, number: int) -> list[tuple[str, int, str]]:
    """`(repository, number, title)` of every open issue blocking `number`; the repository may be another."""
    gh.split_repo(repo)
    items = gh.paginated(f"repos/{repo}/issues/{number}/dependencies/blocked_by")
    return [
        (
            (item.get("repository") or {}).get("full_name") or repo,
            item["number"],
            " ".join((item.get("title") or "").split()),
        )
        for item in items
        if item.get("state") == "open"
    ]


def blocking(repo: str, number: int) -> list[tuple[str, int, str]]:
    """`(repository, number, title)` of every open issue waiting on `number`, by number ascending.

    A story that unblocks others is the one place the process has to look forward: whoever merges it
    is the person who can say what is now ready to be reviewed, and a parked feature that becomes
    several stories has to hand what waited on it to every one of them. The repository may be
    another of the organization's, so it is carried the way `blockers` carries it.
    """
    gh.split_repo(repo)
    items = gh.paginated(f"repos/{repo}/issues/{number}/dependencies/blocking")
    return sorted(
        (
            (
                (item.get("repository") or {}).get("full_name") or repo,
                item["number"],
                " ".join((item.get("title") or "").split()),
            )
            for item in items
            if item.get("state") == "open"
        ),
        key=lambda found: found[1],
    )


def pull_request(repo: str, branch: str) -> tuple[str, str] | None:
    """`(url, state)` of the pull request whose head is `branch`, an open one before a merged one."""
    gh.split_repo(repo)
    data = gh.json_out("pr", "list", "--repo", repo, "--head", branch, "--state", "all", "--json", "url,state")
    items = data if isinstance(data, list) else []
    found = [(str(item.get("url")), str(item.get("state"))) for item in items if isinstance(item, dict)]
    return next((pr for state in ("OPEN", "MERGED") for pr in found if pr[1] == state), None)


def resave_pull_request(repo: str, url: str, body: str) -> None:
    """Save the pull request's body again; GitHub links the issue its footer closes on a save it once missed."""
    gh.split_repo(repo)
    with _body_file(body) as path:
        gh.run("pr", "edit", url, "--repo", repo, "--body-file", path)


def merged_pull_requests(repo: str, limit: int) -> list[tuple[str, str]]:
    """The title and body of the last `limit` merged pull requests, newest first."""
    gh.split_repo(repo)
    data = gh.json_out("pr", "list", "--repo", repo, "--state", "merged", "--limit", str(limit), "--json", "title,body")
    if not isinstance(data, list):
        return []
    return [(item.get("title", ""), item.get("body", "")) for item in data if isinstance(item, dict)]


# One field list for every read of a pull request, so a briefing that wants two of them makes one
# call: `gh.cached` keys on the arguments, and three readers asking for three sets defeated it.
FAILED = ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "ERROR", "STARTUP_FAILURE")
SETTLED = ("SUCCESS", "SKIPPED", "NEUTRAL")


def _check_verdict(check: dict[str, Any]) -> tuple[str, str]:
    """`(name, outcome)` for one check, however the rollup spelled its fields."""
    outcome = check.get("conclusion") or check.get("state") or ""
    name = check.get("name") or check.get("context") or "a check"
    return str(name), outcome.upper()


def _rollup_checks(checks: list[Any]) -> tuple[list[str], int]:
    """`(the names of the checks that failed, how many are still running)` from a status check rollup."""
    failed: list[str] = []
    pending = 0
    for check in checks:
        if not isinstance(check, dict):
            continue
        name, outcome = _check_verdict(check)
        if outcome in FAILED:
            failed.append(name)
        elif outcome not in SETTLED:
            pending += 1
    return failed, pending


BEHIND = {"BEHIND", "DIRTY"}  # main moved on, or the merge would conflict; both need the branch brought up


PR_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){ repository(owner:$owner,name:$name){ "
    "issue(number:$number){ closedByPullRequestsReferences(first:5, includeClosedPrs:true){ nodes{ "
    "number url state merged mergeStateStatus files(first:100){ nodes{ path } } "
    "commits(last:1){ nodes{ commit{ statusCheckRollup{ contexts(first:20){ nodes{ "
    "... on CheckRun{ name conclusion status } ... on StatusContext{ context state } "
    "} } } } } } } } } } }"
)

UNKNOWN_MERGE = "UNKNOWN"


@dataclass(frozen=True)
class PullRequest:
    """One pull request as the issue's own reference to it describes it, read in one call."""

    number: int
    url: str
    state: str  # OPEN, MERGED or CLOSED
    merged: bool
    behind: bool | None  # None while GitHub has not worked the mergeability out yet
    failed_checks: tuple[str, ...]
    pending_checks: int
    files: tuple[str, ...] = ()


def _rollup_contexts(node: dict[str, Any]) -> list[Any]:
    commits = ((node.get("commits") or {}).get("nodes")) or []
    commit = (commits[0].get("commit") if commits and isinstance(commits[0], dict) else None) or {}
    rollup = commit.get("statusCheckRollup") or {}
    return (rollup.get("contexts") or {}).get("nodes") or []


def _pull_request_node(owner: str, name: str, number: int) -> dict[str, Any] | None:
    """The chosen `closedByPullRequestsReferences` node, preferring the one still open."""
    page = gh.graphql(PR_QUERY, {"owner": owner, "name": name, "number": number})[0]
    found = (((page.get("data") or {}).get("repository") or {}).get("issue") or {}).get(
        "closedByPullRequestsReferences"
    ) or {}
    nodes = [node for node in (found.get("nodes") or []) if isinstance(node, dict)]
    if not nodes:
        return None
    for node in nodes:
        if str(node.get("state") or "").upper() == "OPEN":
            return node
    return nodes[0]


def pull_request_for(repo: str, number: int) -> PullRequest | None:
    """The pull request the issue links to, read in one call, or None when it links to none.

    The issue's own reference to its pull request carries the state, whether it merged, its checks,
    and whether main has moved on since, so one call answers what four separate reads used to ask.
    """
    owner, name = gh.split_repo(repo)
    node = _pull_request_node(owner, name, number)
    if node is None:
        return None
    merge_state = str(node.get("mergeStateStatus") or "").upper()
    if merge_state == UNKNOWN_MERGE:
        retried = _pull_request_node(owner, name, number)
        if retried is not None:
            node = retried
            merge_state = str(node.get("mergeStateStatus") or "").upper()
    behind = None if merge_state == UNKNOWN_MERGE else merge_state in BEHIND
    failed, pending = _rollup_checks(_rollup_contexts(node))
    return PullRequest(
        number=int(node.get("number") or 0),
        url=str(node.get("url") or ""),
        state=str(node.get("state") or "").upper(),
        merged=bool(node.get("merged")),
        behind=behind,
        failed_checks=tuple(failed),
        pending_checks=pending,
        files=tuple(str(file.get("path") or "") for file in (node.get("files") or {}).get("nodes") or []),
    )
