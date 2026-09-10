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

VIEW_FIELDS = "number,title,body,url,state,comments"

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


def pull_request(repo: str, branch: str) -> str | None:
    """The URL of the open pull request whose head is `branch`, or None when it has none."""
    gh.split_repo(repo)
    data = gh.json_out("pr", "list", "--repo", repo, "--head", branch, "--state", "open", "--json", "url")
    return data[0].get("url") if isinstance(data, list) and data else None


def pull_request_state(repo: str, url: str) -> str | None:
    """`url` while the pull request there is open, else None; a URL gh cannot read is an error.

    For the pull request the log names: its head may be a branch this clone has never seen, so the
    URL is the one handle that finds it wherever it was pushed from.
    """
    gh.split_repo(repo)
    data = gh.json_out("pr", "view", url, "--repo", repo, "--json", "state,url")
    return data.get("url") if isinstance(data, dict) and data.get("state") == "OPEN" else None
