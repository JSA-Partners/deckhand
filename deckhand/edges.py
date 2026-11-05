"""Every blocker edge between two stories, added or dropped, and the refusals each one raises."""

from __future__ import annotations

from deckhand import gh, issue
from deckhand.step import Refusal, issue_ref, open_issue, ref_label


def _ref(by: str, repo: str) -> tuple[str, int]:
    try:
        return issue_ref(by, repo)
    except ValueError as error:
        raise Refusal(str(error)) from error


def _arg(repo: str, number: int, here: str) -> str:
    """The story as a flag takes it: a bare number in `here`, `owner/name#M` elsewhere."""
    return str(number) if repo == here else f"{repo}#{number}"


def _cycle(here: str, story: tuple[str, int], where: str, blocker: int) -> str | None:
    """The refusal for an edge GitHub refused because its reverse is already held, or None."""
    # A follow-up read that fails must leave GitHub's own error standing, not raise over it.
    try:
        held = [(found_repo, found) for found_repo, found, _ in issue.blockers(where, blocker)]
    except Exception:
        return None
    if story not in held:
        return None
    label = ref_label(where, blocker, here)
    return (
        f"{label} is already blocked by {ref_label(*story, here)}, which is this edge reversed; drop it with "
        f"captain apply --unblock {_arg(where, blocker, here)} --by {_arg(*story, here)}, then add this one"
    )


def _edge(here: str, story: str, by: str) -> tuple[tuple[str, int], tuple[str, int]]:
    """The story and its blocker, each read against `here`, refused when they are one issue."""
    target, blocker = _ref(story, here), _ref(by, here)
    if target == blocker:
        raise Refusal(f"{ref_label(*target, here)} cannot block itself")
    open_issue(*blocker, here)
    return target, blocker


def block(here: str, story: str, by: str) -> None:
    target, (where, blocker) = _edge(here, story, by)
    try:
        issue.add_dependency(*target, (where, blocker))
    except gh.GhError as error:
        raise Refusal(_cycle(here, target, where, blocker) or str(error)) from error
    print(f"{ref_label(*target, here)} blocked by {ref_label(where, blocker, here)}")


def unblock(here: str, story: str, by: str) -> None:
    target, (where, blocker) = _edge(here, story, by)
    issue.remove_dependency(*target, (where, blocker))
    print(f"{ref_label(*target, here)} no longer blocked by {ref_label(where, blocker, here)}")
