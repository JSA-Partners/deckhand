"""The ready step: an approved story goes on the board with its kind, its points, and its blockers.

`context` prints the open blockers, the fields as the board has them, whether a human approved after
the last review, and the analogy table the estimate comes from. Each block degrades to one line of
its own, so a lookup that fails never costs the model the rest of the prompt.

`apply` validates the kind, the points, the review, the approval, and every blocker before it writes
anything, then records the dependencies, adds the item, and sets the fields, printing one line per
write. Status follows the dependencies GitHub holds once those writes are in, not the flags the run
was given, so a story that already waits on something boards Blocked either way. The board's own
automation sets Status after an item is added, asynchronously, so `apply` waits, reads Status back,
and puts it right once if the automation moved it. `DECKHAND_SETTLE` is that wait in seconds; the
tests set it to 0.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from typing import Any

from deckhand import config, fields, gh, issue
from deckhand.config import Settings
from deckhand.fields import format_number
from deckhand.step import (
    Refusal,
    block,
    blockers_block,
    issue_number,
    reason,
    refuse_stub,
    settings_or_error,
    step,
    usable,
)

FIELDS = ("Kind", "Story Points", "Actual")
LIMIT = 20
SETTLE = 2.0
TABLE_HEADER = "| # | Repo | Title | Estimate | Actual | Tasks | Note |"
TABLE_RULE = "| --- | --- | --- | --- | --- | --- | --- |"

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

_TASK_HEADING = re.compile(r"^### Task [0-9]+", re.MULTILINE)
# What gh says when the issue is not there, as opposed to a network, auth, or rate limit failure.
_NOT_FOUND = re.compile(r"not found|could not (find|resolve)|no issue", re.IGNORECASE)


def settle_seconds() -> float:
    """How long to wait before reading Status back; `DECKHAND_SETTLE` overrides the default.

    Never negative, and never a value `time.sleep` would reject: anything that is not a positive
    number is no wait at all.
    """
    raw = os.environ.get("DECKHAND_SETTLE")
    try:
        seconds = float(raw) if raw else SETTLE
    except ValueError:
        seconds = SETTLE
    return seconds if seconds > 0 else 0.0


# --- the analogy table ------------------------------------------------------


def _fmt(value: Any) -> str:
    return "-" if value is None else format_number(value)


def _field_value(node: dict[str, Any], name: str, key: str) -> Any:
    for value in (node.get("fieldValues") or {}).get("nodes") or []:
        if (value.get("field") or {}).get("name") == name:
            return value.get(key)
    return None


def _items(pages: list[Any], root: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in pages:
        project = ((page.get("data") or {}).get(root) or {}).get("projectV2") or {}
        items.extend((project.get("items") or {}).get("nodes") or [])
    return items


def analogy_rows(pages: list[Any], limit: int, root: str = "organization") -> list[str]:
    """Table rows for the most recently closed Done stories across `pages` of the items query.

    `root` is the query's owner root field, `organization` or `user`.
    """
    done = []
    for node in _items(pages, root):
        content = node.get("content")
        if not content or _field_value(node, "Status", "name") != "Done":
            continue
        points = _field_value(node, "Story Points", "number")
        actual = _field_value(node, "Actual", "number")
        done.append((content, points, actual))
    done.sort(key=lambda item: item[0].get("closedAt") or "", reverse=True)
    rows = []
    for content, points, actual in done[:limit]:
        title = (content.get("title") or "").replace("|", "\\|")
        repo = ((content.get("repository") or {}).get("nameWithOwner") or "/").partition("/")[2]
        tasks = len(_TASK_HEADING.findall(content.get("body") or ""))
        note = "uncalibrated" if actual is None else ""
        rows.append(
            f"| {content.get('number')} | {repo} | {title} | {_fmt(points)} | {_fmt(actual)} | {tasks} | {note} |"
        )
    return rows


# --- context ----------------------------------------------------------------


def _date(comment: issue.Comment) -> str:
    return comment.created_at.split("T")[0]


def _fields_block(settings: Settings | Exception, number: int) -> list[str]:
    values = fields.get_fields(usable(settings), gh.repo_slug(), number, FIELDS)
    return [f"  {name}: {'unset' if value is None else value}" for name, value in values.items()]


def _approval(story: issue.Issue) -> str:
    """The one line the approval block prints: who approved, who is waited on, or no review yet."""
    review = issue.review_comment(story)
    if review is None:
        return "no review yet"
    approved = issue.approved_after(story, review)
    if approved is None:
        return f"waiting: no Approved comment after the review of {_date(review)}"
    return f"approved by {approved.author} on {_date(approved)}"


def _approval_block(number: int) -> list[str]:
    return [f"  {_approval(issue.view(gh.repo_slug(), number))}"]


def _table_block(settings: Settings | Exception) -> list[str]:
    resolved = usable(settings)
    query = gh.owner_query(ITEMS_QUERY, resolved.owner_type)
    variables = {"owner": resolved.owner, "number": resolved.project}
    pages = gh.graphql(query, variables, paginate=True)
    rows = analogy_rows(pages, LIMIT, gh.owner_field(resolved.owner_type))
    return [TABLE_HEADER, TABLE_RULE, *rows] if rows else ["  none"]


def context(args: argparse.Namespace) -> int:
    """Print the open blockers, the current fields, the approval, and the Done stories to compare to."""
    settings = settings_or_error()
    block("Blockers:", lambda: blockers_block(gh.repo_slug(), args.issue))
    block("Fields:", lambda: _fields_block(settings, args.issue))
    block("Approval:", lambda: _approval_block(args.issue))
    block(f"Done stories (last {LIMIT}):", lambda: _table_block(settings))
    return 0


# --- apply ------------------------------------------------------------------


def _points(value: str) -> int:
    if not (value.isascii() and value.isdigit()):
        raise Refusal(f"points must be a non-negative integer, got {value!r}")
    return int(value)


def _approved(story: issue.Issue, number: int) -> None:
    """Refuse unless the story carries a review comment with a human approval after it."""
    review = issue.review_comment(story)
    if review is None:
        raise Refusal(f"no review comment on #{number}; run /deckhand:review {number}")
    if issue.approved_after(story, review) is None:
        raise Refusal(f"no Approved comment after the review of {_date(review)}")


def _open_issue(repo: str, number: int) -> None:
    """Refuse unless issue `number` exists and is open; a blocker that is neither blocks nothing.

    Only the sibling read: a blocker's comments are none of this step's business, and its state is
    the half of that read this step acts on.
    """
    try:
        state = issue.sibling(repo, number)[0]
    except gh.GhError as error:
        said = reason(error)
        if _NOT_FOUND.search(said):
            raise Refusal(f"#{number} does not exist") from error
        raise Refusal(f"#{number} cannot be read: {said}") from error
    if state.upper() != "OPEN":
        raise Refusal(f"#{number} is closed")


def _hold_status(settings: Settings, repo: str, number: int, status: str) -> None:
    """Read Status back after the board's automation has had its moment, and put it right once.

    Every write has landed by now, so a failed read is a line to report, not a run to fail.
    """
    time.sleep(settle_seconds())
    try:
        current = fields.get_field(settings, repo, number, "Status")
    except gh.GhError as error:
        print(f"Status not verified ({reason(error)})")
        return
    if current == status:
        return
    fields.set_field(settings, repo, number, "Status", status)
    print(f"Status re-set to {status} (the board's own automation had changed it)")


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kind", required=True, help="the kind of change, one the project defines")
    parser.add_argument("--points", required=True, help="the story points, a non-negative integer")
    parser.add_argument(
        "--blocked-by",
        type=issue_number,
        action="append",
        default=[],
        metavar="N",
        help="an open issue this story waits on; repeat it for more than one",
    )


@step("ready", _configure)
def apply(args: argparse.Namespace) -> int:
    """Put an approved story on the board with its kind, its points, and its blockers."""
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    refuse_stub(args.issue, story.body)
    settings = config.load()
    if args.kind not in settings.kinds:
        raise Refusal(f"kind must be one of: {', '.join(settings.kinds)}")
    points = _points(args.points)
    _approved(story, args.issue)
    blockers = list(dict.fromkeys(args.blocked_by or []))
    if args.issue in blockers:
        raise Refusal(f"#{args.issue} cannot block itself")
    for number in blockers:
        _open_issue(repo, number)
    for number in blockers:
        issue.add_dependency(repo, args.issue, number)
        print(f"Blocked by #{number}")
    # The board follows the dependencies GitHub holds, not the flags: a story split off by an amend
    # already carries one, and it boards Blocked without anyone passing it again.
    status = "Blocked" if blockers or issue.blockers(repo, args.issue) else "Backlog"
    gh.run(
        "project", "item-add", str(settings.project), "--owner", settings.owner, "--url", story.url, "--format", "json"
    )
    print("Added to the board")
    print(fields.set_field(settings, repo, args.issue, "Kind", args.kind))
    print(fields.set_field(settings, repo, args.issue, "Story Points", str(points)))
    print(fields.set_field(settings, repo, args.issue, "Status", status))
    _hold_status(settings, repo, args.issue, status)
    print(f"Next: /deckhand:start {args.issue} when it is at the top of the Backlog.")
    return 0
