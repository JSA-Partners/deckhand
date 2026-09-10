"""The start step: a story in Backlog gets its branch, and the model gets the plan to implement.

`context` prints where the branch is, the open blockers, the story and its scope, the plan, what is
already committed on the branch, the plan references that no longer resolve, and what landed on
`origin/main` since the latest review, and writes nothing to GitHub. The story and the scope are there
because the skill reads the plan against the repository before it branches, and a story that no
longer holds is the one case that sends it back to review.

`apply` refuses a story that is blocked or off the board, then cuts the branch locally from a
freshly fetched `origin/main`, sets In Progress, and logs `Started:` with the pre-build check's
conclusion; nothing is pushed until the branch is reviewed and finished. A branch this clone
already has for the number is checked out instead: with the story In Progress it is being picked
back up, so the status is left alone; still in Backlog, it is the start that failed between the
cut and the board, so the status is written now. The `Started:` entry is owed until the issue's
log has one, whichever run gets that far, so starting is idempotent and the entry appears once. A
story is cut only from Backlog and resumed only from Backlog or In Progress; any other column is a
refusal naming it. Whoever ran it is then assigned the issue, on both paths, so the board says who
has it. Both verbs end with the same plan, commits, drift, and landed blocks, because the model
needs them either way.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from deckhand import config, drift, fields, gh, git, issue, log, sections
from deckhand.config import Settings
from deckhand.step import (
    MAIN,
    Refusal,
    block,
    blockers_block,
    branch_for,
    indented,
    local_branch,
    reason,
    refuse_git,
    refuse_stub,
    settings_or_error,
    step,
    trunk,
    usable,
)

# --- the branch -------------------------------------------------------------


def _branch_name(settings: Settings, kind: str | None, title: str, number: int) -> tuple[str, bool]:
    """`(branch, found)`: the branch this clone has for the story, else the name it gets and False.

    Refuses without a Kind or a slug, and when the clone has two branches for the number.
    """
    try:
        found = local_branch(number)
        return (found, True) if found else (branch_for(settings, kind, title, number), False)
    except ValueError as error:
        raise Refusal(str(error)) from error


def _start_branch(branch: str) -> None:
    """Branch from `origin/main`, freshly fetched; nothing is pushed until the branch is reviewed."""
    refuse_git("fetch", "origin", MAIN)
    refuse_git("checkout", "-b", branch, f"origin/{MAIN}")
    print(f"Branch {branch} created from origin/{MAIN}")


# --- the blocks both verbs print --------------------------------------------


def _commits_block() -> list[str]:
    """What is already committed on this branch, so a story picked back up skips those tasks."""
    return indented(git.run("log", "--reverse", f"{trunk()}..HEAD", "--format=%h %s").splitlines())


def _drift_block(text: str) -> list[str]:
    """The plan references that no longer resolve under the working directory."""
    problems = drift.drift_text(text, Path.cwd())
    return indented(f"{reference}  {said}" for reference, said in problems)


LANDED = 30  # the commits the block lists before it points at git log for the rest


def _landed_block(story: issue.Issue | Exception) -> list[str]:
    """What reached origin/main since the latest review, so the plan is read against today's code.

    A review with no date would make `--since=` list all of main as if it had landed since, so it
    is said instead; past `LANDED` commits the block names the command that lists them all.
    """
    entry = log.last(usable(story), "Review:")
    if entry is None:
        return indented([], "no review entry to date from")
    if not entry.created_at:
        return indented([], "the review entry has no date")
    try:
        git.run("fetch", "origin", MAIN)
    except git.GitError:
        pass  # the tracking ref the clone already has still answers
    since = f"--since={entry.created_at}"
    landed = git.run("log", since, "--format=%h %s", f"-{LANDED + 1}", trunk()).splitlines()
    if len(landed) > LANDED:
        landed[LANDED:] = [f"... more: git log {since} origin/{MAIN}"]
    return indented(landed)


def _report(story: issue.Issue | Exception) -> None:
    """Print the plan, the commits on the branch, the stale references, and what landed on main."""

    def plan_text() -> str:
        return sections.get(usable(story).body, "Plan").strip("\n")

    block("## Plan", lambda: plan_text().splitlines() or ["  none"])
    block("## Commits", _commits_block)
    block("## Plan drift", lambda: _drift_block(plan_text()))
    block("## Landed on main since the review", lambda: _landed_block(story))


# --- context ----------------------------------------------------------------


def _story(number: int) -> issue.Issue | Exception:
    """The story, or the failure to read it, so both blocks that need it share the one lookup."""
    try:
        return issue.view(gh.repo_slug(), number)
    except Exception as error:
        return error


def _branch_line(settings: Settings | Exception, story: issue.Issue | Exception, number: int) -> str:
    """`Branch: <name> (local|none)`."""
    try:
        resolved = usable(settings)
        repo = gh.repo_slug()
        kind = fields.get_fields(resolved, repo, number, ("Kind",))["Kind"]
        branch, found = _branch_name(resolved, kind, usable(story).title, number)
    except Exception as error:  # the blockers and the plan are the blocks the model needs most
        return f"Branch: unavailable ({reason(error)})"
    return f"Branch: {branch} ({'local' if found else 'none'})"


def _agreement(story: issue.Issue | Exception) -> None:
    """The Story and the Scope: what the session judges still holds before the branch is made."""

    def text(name: str) -> list[str]:
        return indented(sections.get(usable(story).body, name, "").splitlines())

    block("## Story", lambda: text("Story"))
    block("## Scope", lambda: text("Scope"))


def context(args: argparse.Namespace) -> int:
    """Print the branch, the blockers, the story and its scope, the plan, the commits, the drift, and what landed."""
    settings = settings_or_error()
    story = _story(args.issue)
    print(_branch_line(settings, story, args.issue))
    block("Blockers:", lambda: blockers_block(gh.repo_slug(), args.issue))
    _agreement(story)
    _report(story)
    return 0


# --- apply ------------------------------------------------------------------


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--note", required=True, help="the pre-build check's conclusion, logged as Started:")


@step("start", _configure)
def apply(args: argparse.Namespace) -> int:
    """Branch a story locally from origin/main, or check out the branch it already has, and log the start."""
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    refuse_stub(args.issue, story.body)
    open_blockers = issue.blockers(repo, args.issue)
    if open_blockers:
        raise Refusal("blocked by " + "; ".join(f"#{number} {title}" for number, title in open_blockers))
    note = " ".join(args.note.split())
    if not note:
        raise Refusal("--note needs the pre-build check's conclusion")
    settings = config.load()
    values = fields.get_fields(settings, repo, args.issue, ("Status", "Kind"))
    branch, found = _branch_name(settings, values["Kind"], story.title, args.issue)
    status = values["Status"]
    # A branch the clone has while the story is still Backlog is a start that failed between the
    # cut and the board, so the status is still owed; a branch with the story In Progress is one
    # being picked back up. The log entry is owed until the issue has it, whichever run got there.
    fresh = not found or status == "Backlog"
    owed = log.last(story, "Started:") is None
    if found:
        if status not in ("Backlog", "In Progress"):
            raise Refusal(f"#{args.issue} has branch {branch} but is {status or 'off the board'}")
        refuse_git("checkout", branch)
        print(f"Existing branch {branch}; {'finishing the interrupted start' if fresh else 'status unchanged'}")
    elif status is None:
        raise Refusal(f"#{args.issue} is off the board; board it first with /deckhand:next {args.issue}")
    elif status != "Backlog":
        raise Refusal(f"#{args.issue} is {status}; a branch is cut only from Backlog")
    else:
        _start_branch(branch)
    if fresh:
        print(fields.set_field(settings, repo, args.issue, "Status", "In Progress"))
    # Bookkeeping, so it comes after the status write the step exists to make: whoever started the
    # story owns it, and adding an assignee GitHub already has changes nothing.
    issue.assign(repo, args.issue)
    print("Assigned @me")
    if owed:
        issue.comment(repo, args.issue, log.checked(f"Started: {note}"))
        print("Logged Started")
    _report(story)
    return 0
