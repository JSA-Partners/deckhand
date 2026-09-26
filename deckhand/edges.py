"""Every blocker edge between two stories, added or dropped, and the refusals each one raises."""

from __future__ import annotations

from deckhand import gh, issue
from deckhand.step import Refusal, issue_ref, open_issue, ref_label


def _ref(by: str, repo: str) -> tuple[str, int]:
    try:
        return issue_ref(by, repo)
    except ValueError as error:
        raise Refusal(str(error)) from error


def _cycle(repo: str, number: int, where: str, blocker: int) -> str | None:
    """The refusal for an edge GitHub refused because its reverse is already held, or None."""
    # A follow-up read that fails must leave GitHub's own error standing, not raise over it.
    try:
        held = [(found_repo, found) for found_repo, found, _ in issue.blockers(where, blocker)]
    except Exception:
        return None
    if (repo, number) not in held:
        return None
    label = ref_label(where, blocker, repo)
    if where != repo:
        return f"{label} is already blocked by #{number}, which is this edge reversed; drop that edge first"
    return (
        f"{label} is already blocked by #{number}, which is this edge reversed; drop it with "
        f"captain apply --unblock {blocker} --by {number}, then add this one"
    )


def block(repo: str, number: int, by: str) -> None:
    where, blocker = _ref(by, repo)
    if (where, blocker) == (repo, number):
        raise Refusal(f"#{number} cannot block itself")
    open_issue(where, blocker, repo)
    try:
        issue.add_dependency(repo, number, (where, blocker))
    except gh.GhError as error:
        raise Refusal(_cycle(repo, number, where, blocker) or str(error)) from error
    print(f"#{number} blocked by {ref_label(where, blocker, repo)}")


def unblock(repo: str, number: int, by: str) -> None:
    where, blocker = _ref(by, repo)
    if (where, blocker) == (repo, number):
        raise Refusal(f"#{number} cannot block itself")
    open_issue(where, blocker, repo)
    issue.remove_dependency(repo, number, (where, blocker))
    print(f"#{number} no longer blocked by {ref_label(where, blocker, repo)}")
