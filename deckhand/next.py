"""The next step: read where a story is and run the one step that is due.

`deckhand next context N` reads the facts GitHub and the clone already hold, picks the step by a
fixed table, and prints the step, why, that step's own instructions from its skill file, and the
step's context. It has no `apply`: every write belongs to the step it dispatches, so nothing here
can be half done.

A fact that could not be read is never guessed at. Each one is read on its own and named when it
fails, and a story missing any of them stops with what could not be read rather than being sent to
a step chosen from half the answer; the command itself still exits 0 with one line per failure.
"""

from __future__ import annotations

import argparse
import importlib
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

from deckhand import config, fields, gh, git, issue, stub
from deckhand.step import ORIGIN_MAIN, PLUGIN_ROOT, branch_for, reason, step

SKILLS = PLUGIN_ROOT / "skills"
# The step whose instructions and context are another step's: a stub is written by `new`.
SKILL_OF = {"write": "new"}
MENU = (
    "Ask one question: Keep building, or Review and finish. "
    "Keep building runs the start step; Review and finish runs the finish step."
)
# The placeholder Claude Code fills in a skill file, which no shell run from here would resolve.
LAUNCHER = "${CLAUDE_PLUGIN_ROOT}/bin/deckhand"
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_INJECTION = re.compile(r"^!`.*`\s*$\n?", re.MULTILINE)


class Facts(NamedTuple):
    """Everything the table reads: what GitHub says about the story, and what the clone says."""

    closed: bool
    stub: bool
    status: str | None  # None when off the board
    branch: str | None  # None when the story has no Kind to make one from
    branch_commits: int | None  # None when origin has no branch
    pull_request: str | None
    reviewed: bool
    feedback: bool
    unavailable: tuple[str, ...] = ()  # the facts whose read failed, in the order they were tried


def decide(number: int, f: Facts) -> tuple[str, str]:
    """`(step, reason)` by the first row that matches; the order is the design's table."""
    if f.closed:
        return "done", f"#{number} is closed."
    if f.unavailable:
        return "stop", f"Cannot read {', '.join(f.unavailable)}; nothing decided."
    if f.pull_request:
        return "merge", f"Pull request open: {f.pull_request}"
    if f.status == "In Progress" and f.branch_commits:
        return "choose", f"Branch {f.branch} has {f.branch_commits} commits."
    if f.status == "In Progress":
        return "start", "Started, nothing built yet."
    if f.status == "Backlog" and f.feedback:
        return "amend", "Feedback waiting on the issue."
    if f.status == "Backlog":
        return "start", "On the board."
    # Pending Review and Done are the board's own columns, and nothing here reboards a story out of
    # one: the pull request row above is the only way back in.
    if f.status is not None:
        return "stop", f"#{number} is {f.status} with no open pull request; nothing decided."
    if f.stub:
        return "write", f"#{number} is a stub."
    if not f.reviewed:
        return "review", "Not reviewed."
    if f.feedback:
        return "amend", "Feedback waiting on the issue."
    return "ready", "Reviewed, nothing waiting."


def launcher() -> str:
    """The deckhand this process was run as, resolved, so a command printed here can be run as it is."""
    return str(Path(sys.argv[0]).resolve())


def instructions(name: str, number: int) -> str:
    """The step skill's body with its frontmatter and injection line removed and `$issue` filled in.

    The skill file is the one place a step's instructions are written; printing it here is what
    lets the steps stay out of the person's menu without their words being duplicated. The launcher
    a skill names is a placeholder Claude Code fills, so it is filled in here too: what is printed
    is read as a command to run, and an unexpanded variable would run nothing.
    """
    text = (SKILLS / SKILL_OF.get(name, name) / "SKILL.md").read_text(encoding="utf-8")
    text = _FRONTMATTER.sub("", text, count=1)
    text = _INJECTION.sub("", text)
    text = text.replace(LAUNCHER, launcher())
    return text.replace("$issue", str(number)).replace("$source", str(number)).strip("\n")


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


def _board(repo: str, number: int) -> dict[str, str | None]:
    """The Status the table reads and the Kind the branch name is made from, in one query."""
    return fields.get_fields(config.load(), repo, number, ("Status", "Kind"))


def _branch(kind: str, title: str, number: int) -> tuple[str, int | None]:
    """`(branch, commits past origin/main)`; the count is None when origin has no such branch.

    A fetch that fails is swallowed: the tracking refs a clone already has still answer the
    question, and a network that is down must not decide that a started story never started.
    """
    name = branch_for(config.load(), kind, title, number)
    try:
        git.run("fetch", "origin")
    except git.GitError:
        pass
    ref = f"origin/{name}"
    try:
        git.run("rev-parse", "--verify", "--quiet", ref)
    except git.GitError:
        return name, None
    return name, int(git.run("rev-list", "--count", f"{ORIGIN_MAIN}..{ref}"))


def _feedback(repo: str, number: int) -> bool:
    """Whether a person has ticked or written since the review the last amend answered.

    Ticking a finding edits the review comment, so a tick counts once: an amend posts its own
    comment, and an edit older than that comment has already been applied. A review nobody has
    touched carries the stamp it was posted with, which is not later than itself, so posting a
    review is never read as an answer to it.
    """
    edited, reviewed_at, amended, replies = issue.feedback_state(repo, number)
    return (edited or "") > max(amended, reviewed_at) or bool(replies)


def _facts(repo: str | None, number: int, story: issue.Issue | None, reader: Reader) -> Facts:
    """Read the table's facts; each one is read only when everything it is derived from was."""
    values = reader.read("board", lambda: _board(repo, number)) if repo and story else None
    kind = values["Kind"] if values else None
    # No Kind means no branch, which is an answer rather than a failure: nothing has branched yet.
    found = reader.read("branch", lambda: _branch(kind, story.title, number)) if kind and story else None
    branch, commits = found if found else (None, None)
    # Only a branch origin has can be the head of a pull request.
    pull = reader.read("pull request", lambda: issue.pull_request(repo, branch)) if commits is not None else None
    reviewed = story is not None and issue.review_comment(story) is not None
    feedback = bool(reader.read("comments", lambda: _feedback(repo, number))) if reviewed else False
    return Facts(
        closed=story is not None and story.state.upper() == "CLOSED",
        stub=story is not None and stub.is_stub(story.body),
        status=values["Status"] if values else None,
        branch=branch,
        branch_commits=commits,
        pull_request=pull,
        reviewed=reviewed,
        feedback=feedback,
        unavailable=tuple(reader.missing),
    )


# --- context -----------------------------------------------------------------


def _module(name: str) -> ModuleType:
    """The step module whose `context` this step runs."""
    return importlib.import_module(f"deckhand.{SKILL_OF.get(name, name)}")


def _instructions_block(name: str, number: int) -> str:
    """The step's instructions, or one line when its skill file cannot be read."""
    try:
        return instructions(name, number)
    except Exception as error:
        return f"  unavailable ({reason(error)})"


def _dispatch(name: str, number: int) -> None:
    """The step's own instructions and the context its command prints, under one heading each."""
    print()
    print("## Instructions")
    print(_instructions_block(name, number))
    print()
    print("## Context")
    # The step's own guard is on its command, not its function, so the call gets one here.
    try:
        _module(name).context(argparse.Namespace(issue=number, source=str(number)))
    except Exception as error:
        print(f"  unavailable ({reason(error)})")


def _choose(number: int) -> None:
    """The one question a person answers on a branch that has commits, and both answers' steps.

    Each answer leads with the command that prints that step's own context: neither step's context
    is above, because the story was not sent to a step, and instructions read against the wrong
    context are worse than none.
    """
    print(MENU)
    answers = (
        ("## If keep building", "start", f'Run "{launcher()}" start context {number} first, then:'),
        ("## If finish", "finish", f'Check out the branch, then run "{launcher()}" finish context {number}, then:'),
    )
    for heading, name, lead in answers:
        print()
        print(heading)
        print()
        print(lead)
        print()
        print(_instructions_block(name, number))


def _done(repo: str, number: int, story: issue.Issue) -> None:
    """The closed story to look at, and the stories it unblocks, each its own next command."""
    print(f"Issue: {story.url}")
    try:
        waiting = issue.blocking(repo, number)
    except Exception as error:
        print(f"Blocked issues: unavailable ({reason(error)})")
        return
    for blocked in waiting:
        print(f"Next: /deckhand:next {blocked}")
    if not waiting:
        print("Next: nothing.")


@step("next")
def context(args: argparse.Namespace) -> int:
    """Print the step the story is due, why, and the instructions and context that step needs."""
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
    if name == "choose":
        _choose(number)
    elif name == "merge":
        print("Merge the pull request on GitHub.")
        print(f"Next: /deckhand:next {number} after it merges.")
    elif name == "stop":
        # A fact that could not be read is worth another try; a column this command cannot act on
        # is the board's own business, and there is nothing to come back to.
        print(f"Next: /deckhand:next {number} when it can be read." if facts.unavailable else "Next: nothing.")
    elif name == "done" and repo and story:
        _done(repo, number, story)
    elif name != "done":
        _dispatch(name, number)
    return 0
