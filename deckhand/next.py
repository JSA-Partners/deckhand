"""The next step: read where a story is and say what is due, with the context that step needs.

`deckhand next context N` reads the facts GitHub and the clone hold: the issue, its column, its log,
the branch this clone has for it, and any open pull request. A fixed table picks the step, and the
command prints a briefing: the step, why, the story's title, the last lines of the log, and the
step's own context. The skill that injects it carries the guidance for every step and carries the
story on from one to the next; this command has no `apply`, so nothing here can be half done.

The clone's worktrees are read too: a branch checked out in another worktree is named as
`Worktree: <path>` so the session moves there, a done story's worktree is removed when the run is
not standing in it, and before the story is read the worktrees of other closed stories are swept,
from the clone only. Nothing here writes to GitHub.

A fact that could not be read is never guessed at: a story missing one stops with what could not
be read rather than being sent to a step chosen from half the answer.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable
from typing import NamedTuple

from deckhand import board, columns, config, fields, gh, git, issue, log, sections, stub, worktree
from deckhand.step import MAIN, branch_for, issue_number, local_branch, reason, ref_label, step, trunk

# The module whose context a step prints; the steps `next` answers itself are absent.
CONTEXT_OF = {
    "after": "after",
    "fix": "start",
    "write": "new",
    "settle": "new",
    "review": "review",
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
    written: bool  # the body is a story rather than a draft's requirements
    branch: str | None  # the clone's branch, or the name start would cut; None without a Kind
    blockers: tuple[str, ...]  # the open blockers, each `<label>  <title>`, printed on the build rows
    commits: int | None  # past what has landed on main; None when this clone has no branch for the story
    pull_request: str | None  # the URL of the open pull request
    failed_checks: tuple[str, ...]  # the pull request's checks that failed, by name
    pending_checks: int  # the pull request's checks still running
    behind: bool  # the pull request is behind main, or in conflict with it
    pull_requested: bool  # a `Pull request:` entry, so a closed story is a merged one
    merged: bool  # the pull request merged; the closed rows above answer a story that is done
    after_merge_left: int
    unavailable: tuple[str, ...] = ()  # the facts whose read failed, in the order they were tried
    parked: bool = False  # a stub with no stories yet: a feature to settle before anything is written


def decide(number: int, f: Facts) -> tuple[str, str]:
    """`(step, reason)` by the first row that matches; the order is the design's table."""
    if f.closed and f.pull_requested and f.after_merge_left:
        items = "item is" if f.after_merge_left == 1 else "items are"
        return "after", f"#{number} is merged; {f.after_merge_left} after-the-merge {items} left."
    if f.closed:
        return "done", f"#{number} is closed."
    if f.unavailable:
        return "stop", f"Cannot read {', '.join(f.unavailable)}; nothing decided."
    if f.pull_request and f.failed_checks:
        return "fix", f"Pull request open; {', '.join(f.failed_checks)} failed."
    if f.pull_request and f.behind:
        return "update", "Pull request open; main has moved on."
    if f.pull_request and f.pending_checks:
        checks = "check is" if f.pending_checks == 1 else "checks are"
        return "merge", f"Pull request open; {f.pending_checks} {checks} still running."
    if f.pull_request:
        return "merge", f"Pull request open: {f.pull_request}"
    if f.merged:
        return "stop", f"#{number} has a merged pull request and an open issue; close the issue on GitHub."
    if f.status == columns.IN_PROGRESS and f.commits:
        return "resume", f"Branch {f.branch} has {f.commits} commits."
    if f.status == columns.IN_PROGRESS and f.commits is None:
        where = f"Branch {f.branch} is not in this clone." if f.branch else "No branch in this clone."
        return "build", where
    if f.status == columns.IN_PROGRESS:
        return "build", "Started, nothing built yet."
    if f.status == columns.BACKLOG and f.blockers:
        named = ", ".join(blocker.split("  ")[0] for blocker in f.blockers)
        return "wait", f"Waits on {named}."
    if f.status == columns.BACKLOG:
        return "check", "On the board; check the plan against the code, then build."
    # In Review and Done are the board's own columns, and nothing here reboards a story out of
    # one: the pull request row above is the only way back in.
    # Refinement and Ready are positions inside the drafting rows below, not columns to stop at:
    # an amend puts a story in one and a review in the other, and both still have a step to run.
    if f.status not in (None, columns.DRAFT, columns.REFINEMENT, columns.READY):
        return "stop", f"#{number} is {f.status} with no open pull request; nothing decided."
    if not f.written and f.parked:
        return "settle", f"#{number} is a parked feature; settle its requirements."
    if not f.written:
        return "write", f"#{number} is a stub."
    # Only the review writes Ready, so the column answers whether it ran; an amend writes Refinement,
    # which lands here again rather than on a row of its own.
    if not columns.at_least(f.status, columns.READY):
        return "review", "Not reviewed."
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
    """The After the merge items whose boxes are still unticked, in the plan's order."""
    return [text for text, ticked in sections.after_merge_items(story.body) if not ticked]


def _linked(repo: str, number: int, branch: str | None) -> issue.PullRequest | None:
    """The story's pull request, from the issue's own reference to it.

    The head lookup is the fallback for one opened by hand, which GitHub does not link to the issue
    and which therefore no reference can name.
    """
    found = issue.pull_request_for(repo, number)
    if found is not None or not branch:
        return found
    url = issue.pull_request(repo, branch)
    return issue.PullRequest(0, url, "OPEN", False, None, (), 0) if url else None


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
    found = reader.read("pull request", lambda: _linked(repo, number, branch)) if repo and story else None
    open_now = found is not None and found.state == "OPEN"
    # Mergeability is worked out lazily, and only an open pull request has a branch to bring up to
    # date, so an unread state is a fact this run is missing rather than one that reads as settled.
    if open_now and found.behind is None:
        reader.missing.append("merge state")
    blockers = (
        reader.read(
            "blockers",
            lambda: tuple(f"{ref_label(where, n, repo)}  {title}" for where, n, title in issue.blockers(repo, number)),
        )
        if repo and story and not closed
        else None
    )
    return Facts(
        closed=closed,
        status=values["Status"] if values else None,
        written=story is not None and not stub.is_stub(story.body),
        branch=branch,
        blockers=blockers or (),
        commits=commits,
        pull_request=found.url if open_now else None,
        failed_checks=found.failed_checks if open_now else (),
        pending_checks=found.pending_checks if open_now else 0,
        behind=bool(found.behind) if open_now else False,
        pull_requested=found is not None,
        merged=found is not None and found.merged,
        after_merge_left=len(_after_merge_left(story)) if story else 0,
        unavailable=tuple(reader.missing),
        parked=story is not None and stub.is_stub(story.body) and not stub.read(story.body)[1],
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
        module.context(argparse.Namespace(issue=number, source=str(number), idea=False, repo=None))
    except Exception as error:
        print(f"  unavailable ({reason(error)})")


def _clear(story: issue.Issue) -> None:
    """What the story still owes before this session is closed, printed on the rows where it is merged.

    The question after a merge is whether anything is left, and three facts answer it: the items
    whose boxes are unticked, work in the checkout that is not committed, and whether the session
    stands in a worktree that the next run from the clone removes.
    """
    owed = []
    left = len(_after_merge_left(story))
    if left:
        items = "item" if left == 1 else "items"
        owed.append(f"{left} after-the-merge {items} left")
    here = None
    try:
        here = git.run("rev-parse", "--show-toplevel")
        if git.run("status", "--porcelain").strip():
            owed.append(f"uncommitted changes in {here}")
    except Exception as error:
        owed.append(f"the checkout could not be read ({reason(error)})")
    print(f"Clear: {', '.join(owed) if owed else 'nothing owed'}")
    try:
        inside = not worktree.from_clone()
    except Exception:
        inside = False  # not a repository: the run's own lines say so, and this note would add nothing
    if inside and here:
        print(f"  this session stands in the worktree {here}; its folder goes on the next run from the clone")


def _next_story(repo: str, number: int) -> None:
    """The oldest open story of the repository on the board, which is the one to take up next."""
    try:
        found = board.oldest_open(config.load(), repo, exclude=number)
    except Exception as error:
        print(f"Next story: unavailable ({reason(error)})")
        return
    print(f"Next story: #{found[0]} {found[1]}" if found else "Next story: none")


def _sweep(repo: str, number: int) -> None:
    """Remove the worktrees of other closed stories, from the clone; a sweep that fails costs nothing."""
    try:
        lines = worktree.sweep(repo, exclude=number)
    except Exception as error:
        lines = [f"Sweep: unavailable ({reason(error)})"]
    for line in lines:
        print(line)


def _worktree(name: str, branch: str | None) -> tuple[str | None, str | None]:
    """`(line, removed)`: the briefing's `Worktree:` line, and what the done row removed.

    The line is printed when the branch is checked out somewhere other than the current directory,
    so the skill moves the session there; on the done row from inside the worktree it says `(here)`
    instead, because git will not remove the directory a session stands in. On the done row from
    anywhere else the worktree, its branch, and its tracking ref go, and the reason says so.
    """
    if not branch:
        return None, None
    try:
        where = worktree.checked_out(branch)
        if where is None:
            return None, None
        if worktree.here(where):
            return (f"Worktree: {where} (here)" if name == "done" else None), None
        if name != "done":
            return f"Worktree: {where}", None
        try:
            worktree.remove(branch, where)
        except git.GitError as error:
            return f"Worktree: {where} (left: {error})", None
        return None, f"Removed worktree {where}."
    except Exception as error:
        return f"Worktree: unavailable ({reason(error)})", None


def _configure_context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("issue", type=issue_number, nargs="?", default=None, help="the issue number")


@step("next", issue_bound=False, configure_context=_configure_context)
def context(args: argparse.Namespace) -> int:
    """Print the step the story is due, why, its title, the log's tail, and the context that step needs."""
    if args.issue is None:
        print("No story named. Run /deckhand:captain for the board, the build order, and what to pick up.")
        return 0
    number = args.issue
    reader = Reader()
    repo = reader.read("repository", gh.repo_slug)
    if repo:
        _sweep(repo, number)
    for line in worktree.catch_up():
        print(line)
    story = reader.read("issue", lambda: issue.view(repo, number)) if repo else None
    facts = _facts(repo, number, story, reader)
    for note in reader.notes:
        print(note)
    name, why = decide(number, facts)
    line, removed = _worktree(name, facts.branch)
    print(f"Step: {name}")
    print(f"{why} {removed}" if removed else why)
    if story:
        print(f"Title: {story.title}")
        print(f"Issue: {story.url}")
    if line:
        print(line)
    if name in ("wait", "build", "resume") and facts.blockers:
        print("Blocked by:")
        for blocker in facts.blockers:
            print(f"  {blocker}")
    _log_block(story)
    if name in CONTEXT_OF:
        _context(name, number)
        # The after context lists the items and names the tick, so only what the session still owes
        # beyond them is added here.
        if name == "after" and story:
            _clear(story)
    elif name == "done" and repo:
        if story:
            _clear(story)
        _next_story(repo, number)
    return 0
