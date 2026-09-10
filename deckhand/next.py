"""The next step: read where a story is and say what is due, with the context that step needs.

`deckhand next context N` reads the facts GitHub and the clone hold: the issue, its column, its log,
the branch this clone has for it, and any open pull request. A fixed table picks the step, and the
command prints a briefing: the step, why, the story's title, the last lines of the log, and the
step's own context. The skill that injects it carries the guidance for every step and carries the
story on from one to the next; this command has no `apply`, so nothing here can be half done.

A fact that could not be read is never guessed at: a story missing one stops with what could not
be read rather than being sent to a step chosen from half the answer.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable
from typing import NamedTuple

from deckhand import board, config, fields, gh, git, issue, log, sections, stub
from deckhand.step import MAIN, branch_for, local_branch, reason, step, trunk

# The module whose context a step prints; the steps `next` answers itself are absent.
CONTEXT_OF = {
    "write": "new",
    "review": "review",
    "reconsider": "amend",
    "board": "ready",
    "check": "start",
    "build": "start",
    "resume": "start",
}
LAST_LINES = 3


class Facts(NamedTuple):
    """Everything the table reads: what GitHub says about the story, and what the clone says."""

    closed: bool
    status: str | None  # None when off the board, which reads as Draft
    drafted: bool  # a body that is not a stub: a `Drafted:` entry with no story behind it is not one
    reviewed: bool  # a `Review:` entry
    amended_since_review: bool  # an `Amended:` entry after the latest `Review:`, so a second review clears it
    branch: str | None  # the clone's branch, or the name start would cut; None without a Kind
    commits: int | None  # past what has landed on main; None when this clone has no branch for the story
    pull_request: str | None  # the URL of the open pull request
    pull_requested: bool  # a `Pull request:` entry, so a closed story is a merged one
    after_merge_left: int
    unavailable: tuple[str, ...] = ()  # the facts whose read failed, in the order they were tried


def decide(number: int, f: Facts) -> tuple[str, str]:
    """`(step, reason)` by the first row that matches; the order is the design's table."""
    if f.closed and f.pull_requested and f.after_merge_left:
        items = "item is" if f.after_merge_left == 1 else "items are"
        return "after", f"#{number} is merged; {f.after_merge_left} after-the-merge {items} left."
    if f.closed:
        return "done", f"#{number} is closed."
    if f.unavailable:
        return "stop", f"Cannot read {', '.join(f.unavailable)}; nothing decided."
    if f.pull_request:
        return "merge", f"Pull request open: {f.pull_request}"
    if f.status == "In Progress" and f.commits:
        return "resume", f"Branch {f.branch} has {f.commits} commits."
    if f.status == "In Progress" and f.commits is None:
        where = f"Branch {f.branch} is not in this clone." if f.branch else "No branch in this clone."
        return "build", where
    if f.status == "In Progress":
        return "build", "Started, nothing built yet."
    if f.status == "Backlog":
        return "check", "On the board; check the plan against the code, then build."
    # Pending Review and Done are the board's own columns, and nothing here reboards a story out of
    # one: the pull request row above is the only way back in.
    if f.status not in (None, "Draft"):
        return "stop", f"#{number} is {f.status} with no open pull request; nothing decided."
    if not f.drafted:
        return "write", f"#{number} is a stub."
    if not f.reviewed:
        return "review", "Not reviewed."
    if f.amended_since_review:
        return "reconsider", "Amended since the review."
    return "board", "Reviewed, nothing waiting."


# --- the facts ---------------------------------------------------------------


class Reader:
    """Every fact read once, with the ones that could not be read named rather than guessed at.

    A fact whose prerequisite failed is not attempted and not named: the prerequisite is what the
    person has to fix, and saying it twice would read as two problems.
    """

    def __init__(self) -> None:
        self.notes: list[str] = []
        self.missing: list[str] = []

    def read[T](self, fact: str, fill: Callable[[], T]) -> T | None:
        """`fill()`, or None with `fact` named on stdout and kept for the table."""
        try:
            return fill()
        except Exception as error:
            self.notes.append(f"{fact.capitalize()}: unavailable ({reason(error)})")
            self.missing.append(fact)
            return None


def _branch(kind: str | None, title: str, number: int) -> tuple[str | None, int | None]:
    """`(branch, commits past main)`; None commits when this clone has no branch for the story.

    The branch is found by the story's number, so a retitle after start changes nothing here. With
    none in the clone, the name is the one `start` would cut, derived only to say it and to refuse a
    title with no slug now rather than then; with no Kind yet there is no name, which is an answer.

    The count is past what has landed on main, so main is fetched first, and only main: a stale
    origin/main would count work merged since the branch was cut as the story's own. The fetch that
    fails is swallowed, since the tracking ref the clone already has still answers, and the range is
    the same one `start` prints as the branch's commits.
    """
    name = local_branch(number)
    if name is None:
        return (branch_for(config.load(), kind, title, number) if kind else None), None
    try:
        git.run("fetch", "origin", MAIN)
    except git.GitError:
        pass
    return name, int(git.run("rev-list", "--count", f"{trunk()}..{name}"))


def _after_merge_left(story: issue.Issue) -> list[str]:
    """The After the merge items no `After the merge:` entry has answered yet, in the plan's order."""
    items = sections.after_merge(story.body)
    done = sum(1 for entry in log.entries(story) if entry.prefix == "After the merge:")
    return items[done:]


def _pull_request(repo: str, story: issue.Issue, branch: str | None) -> str | None:
    """The URL of the story's open pull request, or None.

    The log names the pull request `finish` opened, and its URL finds it whatever branch it was
    pushed from; the head lookup is for a story whose log has no entry, from before the log or from
    a pull request opened by hand.
    """
    entry = log.last(story, "Pull request:")
    if entry is not None:
        return issue.pull_request_state(repo, entry.text.split()[0])
    return issue.pull_request(repo, branch) if branch else None


def _facts(repo: str | None, number: int, story: issue.Issue | None, reader: Reader) -> Facts:
    """Read the table's facts; each one is read only when everything it is derived from was."""
    values = (
        reader.read("board", lambda: fields.get_fields(config.load(), repo, number, ("Status", "Kind")))
        if repo and story
        else None
    )
    kind = values["Kind"] if values else None
    found = reader.read("branch", lambda: _branch(kind, story.title, number)) if repo and story else None
    branch, commits = found if found else (None, None)
    closed = story is not None and story.state.upper() == "CLOSED"
    pull = (
        reader.read("pull request", lambda: _pull_request(repo, story, branch))
        if repo and story and not closed
        else None
    )
    reviewed = story is not None and log.last(story, "Review:") is not None
    return Facts(
        closed=closed,
        status=values["Status"] if values else None,
        drafted=story is not None and not stub.is_stub(story.body),
        reviewed=reviewed,
        amended_since_review=reviewed and any(e.prefix == "Amended:" for e in log.since(story, "Review:")),
        branch=branch,
        commits=commits,
        pull_request=pull,
        pull_requested=story is not None and log.last(story, "Pull request:") is not None,
        after_merge_left=len(_after_merge_left(story)) if story else 0,
        unavailable=tuple(reader.missing),
    )


# --- the briefing ------------------------------------------------------------


def _log_block(story: issue.Issue | None) -> None:
    """The last entries of the log, one line each, so the session sees what was decided before."""
    print("Log:")
    entries = log.entries(story)[-LAST_LINES:] if story else []
    for entry in entries:
        print(f"  {entry.created_at[:10] or 'undated'} {entry.prefix} {entry.text}")
    if not entries:
        print("  none")


def _context(name: str, number: int) -> None:
    """The context of the step's own command, under one heading; a failure inside is one line."""
    print()
    print("## Context")
    module = importlib.import_module(f"deckhand.{CONTEXT_OF[name]}")
    # The step's own guard is on its command, not its function, so the call gets one here.
    try:
        module.context(argparse.Namespace(issue=number, source=str(number)))
    except Exception as error:
        print(f"  unavailable ({reason(error)})")


def _after(story: issue.Issue) -> None:
    """The After the merge items still to do, each one a line."""
    print("Left:")
    for item in _after_merge_left(story):
        print(f"  - {item}")


def _next_story(repo: str, number: int) -> None:
    """The oldest open story of the repository on the board, which is the one to take up next."""
    try:
        found = board.oldest_open(config.load(), repo, exclude=number)
    except Exception as error:
        print(f"Next story: unavailable ({reason(error)})")
        return
    print(f"Next story: #{found[0]} {found[1]}" if found else "Next story: none")


@step("next")
def context(args: argparse.Namespace) -> int:
    """Print the step the story is due, why, its title, the log's tail, and the context that step needs."""
    number = args.issue
    reader = Reader()
    repo = reader.read("repository", gh.repo_slug)
    story = reader.read("issue", lambda: issue.view(repo, number)) if repo else None
    facts = _facts(repo, number, story, reader)
    for note in reader.notes:
        print(note)
    name, why = decide(number, facts)
    print(f"Step: {name}")
    print(why)
    if story:
        print(f"Title: {story.title}")
        print(f"Issue: {story.url}")
    _log_block(story)
    if name in CONTEXT_OF:
        _context(name, number)
    elif name == "after" and story:
        _after(story)
    elif name == "done" and repo:
        _next_story(repo, number)
    return 0
