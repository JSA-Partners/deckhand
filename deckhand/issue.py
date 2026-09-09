"""Every read and write of a GitHub issue: the body, its comments, and its blocking dependencies.

Bodies always travel through a temp file, so quoting never matters and a body of any size works.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from deckhand import gh

REVIEW_HEADING = "## Review"
VIEW_FIELDS = "number,title,body,url,state,comments"
# The comments the process writes itself; everything else on an issue was written by a person.
PROCESS_PREFIXES = ("Amended:", "Deviation:", "Split:", REVIEW_HEADING)

_ISSUE_URL = re.compile(r"https://\S+/issues/([0-9]+)(?:#\S+)?")


@dataclass(frozen=True)
class Comment:
    """One issue comment. `url` is None when it came from `gh issue view`, which omits it."""

    author: str
    body: str
    created_at: str
    url: str | None = None


@dataclass(frozen=True)
class Issue:
    """An issue as the process reads it: the body every step edits and every comment on it."""

    number: int
    title: str
    body: str
    url: str
    state: str
    comments: list[Comment]


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
        url=raw.get("url"),
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
    )


def sibling(repo: str, number: int) -> tuple[str, str]:
    """Another issue's `(state, body)`; all a caller needs to say where that issue has got to."""
    gh.split_repo(repo)
    data = gh.json_out("issue", "view", str(number), "--repo", repo, "--json", "state,body")
    return (data.get("state") or "", data.get("body") or "")


def create(repo: str, title: str, body: str) -> tuple[int, str]:
    """Create an issue; returns its `(number, url)`."""
    gh.split_repo(repo)
    with _body_file(body) as path:
        output = gh.run("issue", "create", "--repo", repo, "--title", title, "--body-file", path)
    return _parsed_url(output, "issue create")


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


def add_dependency(repo: str, number: int, blocked_by: int) -> None:
    """Record that `number` is blocked by `blocked_by`; the API takes the blocker's database id."""
    gh.split_repo(repo)
    blocker = gh.issue_id(repo, blocked_by)
    gh.run(
        "api",
        "-X",
        "POST",
        f"repos/{repo}/issues/{number}/dependencies/blocked_by",
        "-F",
        f"issue_id={blocker}",
    )


def blockers(repo: str, number: int) -> list[tuple[int, str]]:
    """The `(number, title)` of every open issue blocking `number`."""
    gh.split_repo(repo)
    items = gh.paginated(f"repos/{repo}/issues/{number}/dependencies/blocked_by")
    return [
        (item["number"], " ".join((item.get("title") or "").split())) for item in items if item.get("state") == "open"
    ]


def blocking(repo: str, number: int) -> list[int]:
    """The numbers of the open issues `number` blocks, in ascending order.

    A story that unblocks others is the one place the process has to look forward: whoever merges it
    is the person who can say what is now ready to be reviewed.
    """
    gh.split_repo(repo)
    items = gh.paginated(f"repos/{repo}/issues/{number}/dependencies/blocking")
    return sorted(item["number"] for item in items if item.get("state") == "open")


def _heading(body: str) -> str:
    """The comment's first non-blank line, stripped."""
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _is_review(body: str) -> bool:
    """Whether a comment is a review the process posted.

    The heading has to be the whole line: the old process headed its passes `## Review pass: spec`,
    and one of those on an issue is not a review this process can read ticks off.
    """
    return _heading(body) == REVIEW_HEADING


def review_comment(issue: Issue) -> Comment | None:
    """The issue's last review comment in list order, or None when no review has been posted."""
    for candidate in reversed(issue.comments):
        if _is_review(candidate.body):
            return candidate
    return None


def pull_request(repo: str, branch: str) -> str | None:
    """The URL of the open pull request whose head is `branch`, or None when it has none."""
    gh.split_repo(repo)
    data = gh.json_out("pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "url")
    return data[0].get("url") if isinstance(data, list) and data else None


def _rest_comments(repo: str, number: int) -> list[dict[str, Any]]:
    """Every comment as the REST API reports it, oldest first.

    `gh issue view` gives no edit stamp and no id, and an edit is how a person ticks a finding, so
    the process reads the comments a second way when it needs to know that one was touched.
    """
    gh.split_repo(repo)
    return [raw for raw in gh.paginated(f"repos/{repo}/issues/{number}/comments") if isinstance(raw, dict)]


def _amended(body: str) -> bool:
    """Whether a comment is the record an amend posts."""
    return _heading(body).startswith("Amended:")


def feedback_state(repo: str, number: int) -> tuple[str | None, str, str, list[Comment]]:
    """`(review edited at, review posted at, last amended at, the comments since)`, from one read.

    The four answer one question between them, and asking it four times would be four round trips
    for the same list: has anyone said something the last amend has not already answered. Ticking a
    finding edits the review comment rather than adding one, so the edit stamp is the only trace a
    tick leaves, and the stamp the review was posted with is what tells an edit from a review that
    nobody has touched.
    """
    raws = _rest_comments(repo, number)
    edited: str | None = None
    reviewed_at = ""
    amended = ""
    for raw in raws:
        body = raw.get("body") or ""
        created = raw.get("created_at") or ""
        if _is_review(body) and created >= reviewed_at:
            reviewed_at, edited = created, raw.get("updated_at")
        elif _amended(body):
            amended = max(amended, created)
    cutoff = max(reviewed_at, amended)
    since = [
        Comment(
            author=(raw.get("user") or {}).get("login") or "",
            body=raw.get("body") or "",
            created_at=raw.get("created_at") or "",
            url=raw.get("html_url"),
        )
        for raw in raws
        if (raw.get("created_at") or "") > cutoff and not _heading(raw.get("body") or "").startswith(PROCESS_PREFIXES)
    ]
    return edited, reviewed_at, amended, since


def feedback_since(repo: str, number: int) -> list[Comment]:
    """Comments a person wrote after the later of the review and the last `Amended:` comment."""
    return feedback_state(repo, number)[3]
