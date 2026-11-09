"""Every blocker edge between two stories, added or dropped, and the refusals each one raises."""

from __future__ import annotations

from deckhand import gh, issue
from deckhand.step import Refusal, issue_ref, open_issue, reason, ref_label


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


def _key(story: tuple[str, int]) -> tuple[str, int]:
    return story[0].lower(), story[1]


def loop(story: tuple[str, int], blocker: tuple[str, int]) -> list[tuple[str, int]]:
    """The path `blocker -> ... -> story` through open blockers, in any repository, or empty.

    A path means `story` waiting on `blocker` would close a loop. GitHub refuses only the reverse of
    an edge within one repository, so the walk is the one check across repositories.
    """
    came: dict[tuple[str, int], tuple[str, int] | None] = {_key(blocker): None}
    named = {_key(blocker): blocker}
    queue = [blocker]
    while queue:
        node = queue.pop(0)
        for where, number, _ in issue.blockers(*node):
            found = (where, number)
            if _key(found) in came:
                continue
            came[_key(found)], named[_key(found)] = _key(node), found
            if _key(found) == _key(story):
                path, at = [], _key(found)
                while at is not None:
                    path.append(named[at])
                    at = came[at]
                return path[::-1]
            queue.append(found)
    return []


def refuse_loop(here: str, story: tuple[str, int], blocker: tuple[str, int]) -> None:
    """Refuse, before any write, an edge from `story` to `blocker` that would close a loop."""
    try:
        path = loop(story, blocker)
    except Exception as error:
        raise Refusal(f"cannot check {ref_label(*blocker, here)} for a loop: {reason(error)}") from error
    if not path:
        return
    chain = " -> ".join(ref_label(*node, here) for node in [story, *path])
    raise Refusal(
        f"{ref_label(*path[0], here)} is already blocked by {ref_label(*path[1], here)}, so "
        f"{ref_label(*story, here)} waiting on {ref_label(*blocker, here)} closes a loop: {chain}; drop one "
        f"edge first with captain apply --unblock {_arg(*path[0], here)} --by {_arg(*path[1], here)}"
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
    refuse_loop(here, target, (where, blocker))
    try:
        issue.add_dependency(*target, (where, blocker))
    except gh.GhError as error:
        raise Refusal(_cycle(here, target, where, blocker) or str(error)) from error
    print(f"{ref_label(*target, here)} blocked by {ref_label(where, blocker, here)}")


def unblock(here: str, story: str, by: str) -> None:
    target, (where, blocker) = _edge(here, story, by)
    issue.remove_dependency(*target, (where, blocker))
    print(f"{ref_label(*target, here)} no longer blocked by {ref_label(where, blocker, here)}")
