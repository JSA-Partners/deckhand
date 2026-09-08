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

_ISSUE_URL = re.compile(r"https://\S+/issues/([0-9]+)(?:#\S+)?")
# Leading bold or italic markers are decoration, not the word: **Approved** approves.
_FIRST_WORD = re.compile(r"[\s*_]*([A-Za-z]+)")


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


def _heading(body: str) -> str:
    """The comment's first non-blank line, stripped."""
    for line in body.splitlines():
        if line.strip():
            return line.strip()
    return ""


def review_comment(issue: Issue) -> Comment | None:
    """The issue's last review comment in list order, or None when no review has been posted."""
    for candidate in reversed(issue.comments):
        if _heading(candidate.body) == REVIEW_HEADING:
            return candidate
    return None


def approved_after(issue: Issue, comment: Comment) -> Comment | None:
    """The latest comment posted after `comment` that opens with the word `approved`."""
    origin = next((i for i, c in enumerate(issue.comments) if c is comment or c == comment), -1)
    for position in range(len(issue.comments) - 1, -1, -1):
        candidate = issue.comments[position]
        # gh stamps every comment ...Z at second precision, so string order is time order; a tie
        # goes to list position, and an approval in the same second as the review still counts.
        if (candidate.created_at, position) <= (comment.created_at, origin):
            continue
        word = _FIRST_WORD.match(candidate.body)
        if word is not None and word.group(1).lower() == "approved":
            return candidate
    return None
