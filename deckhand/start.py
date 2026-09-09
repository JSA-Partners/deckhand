"""The start step: a story on the board gets its branch, and the model gets the plan to implement.

`context` prints where the branch is, the open blockers, the story and its scope, the plan, what is
already committed on the branch, and the plan references that no longer resolve, and never writes
anything. The story and the scope are there because the skill reads the plan against the repository
before it branches, and a story that no longer holds is the one case that sends it back to review.

`apply` refuses a story that is blocked or off the board, then puts the branch on origin: created
from `origin/main`, or pushed from the local branch an interrupted run left behind, and sets In
Progress either way. A branch origin already has is a story being picked back up, so it is checked
out, fast-forwarded, and the status left alone. Starting is therefore idempotent, and the status
write follows the push: a push that fails leaves the board saying the story never started. Whoever
ran it is then assigned the issue, on both paths, so the board says who has it. Both verbs end with
the same plan, commits, and drift blocks, because the model needs them either way.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from deckhand import config, drift, fields, gh, git, issue, sections
from deckhand.config import Settings
from deckhand.step import (
    MAIN,
    Refusal,
    block,
    blockers_block,
    branch_for,
    indented,
    reason,
    refuse_git,
    refuse_stub,
    settings_or_error,
    step,
    trunk,
    usable,
)


def _ok(*args: str) -> bool:
    """Whether the git command exits 0; for the questions where a non-zero exit is the answer."""
    try:
        git.run(*args)
    except git.GitError:
        return False
    return True


# --- the branch -------------------------------------------------------------


def _exists(branch: str) -> tuple[bool, bool]:
    """`(local, origin)`: whether the branch is a local branch, and whether origin already has it."""
    local = _ok("rev-parse", "--verify", "--quiet", branch)
    return local, bool(refuse_git("ls-remote", "--heads", "origin", branch))


def _where(local: bool, remote: bool) -> str:
    """`local`, `origin`, or `none`: the nearest place the branch already exists."""
    return "local" if local else "origin" if remote else "none"


def _branch_name(settings: Settings, kind: str | None, title: str, number: int) -> str:
    """The story's branch name; refuses when the board has no Kind or the title yields no slug."""
    try:
        return branch_for(settings, kind, title, number)
    except ValueError as error:
        raise Refusal(str(error)) from error


def _push(branch: str) -> None:
    """Put the branch on origin. Past the refusal point: a failure here reaches cli.main."""
    git.run("push", "-u", "origin", branch)
    print("Pushed")


def _start_branch(branch: str, local: bool) -> None:
    """Branch from `origin/main`, or take up the branch an interrupted run left unpushed, and push."""
    if local:
        refuse_git("checkout", branch)
        print(f"Branch {branch} is local only; finishing the interrupted start")
    else:
        refuse_git("fetch", "origin", MAIN)
        refuse_git("checkout", "-b", branch, f"origin/{MAIN}")
        print(f"Branch {branch} created from origin/{MAIN}")
    _push(branch)


def _checkout(branch: str, local: bool) -> None:
    """Check out the branch origin has, at the commit origin has it at."""
    if not local:
        refuse_git("fetch", "origin")  # the checkout branches from the tracking ref the fetch writes
    refuse_git("checkout", branch)
    if local:
        # Named, because a branch pushed without -u tracks nothing and a bare pull would not know
        # where to look. Past the refusal point: HEAD has already moved.
        git.run("pull", "--ff-only", "origin", branch)


# --- the blocks both verbs print --------------------------------------------


def _commits_block() -> list[str]:
    """What is already committed on this branch, so a story picked back up skips those tasks."""
    return indented(git.run("log", "--reverse", f"{trunk()}..HEAD", "--format=%h %s").splitlines())


def _drift_block(text: str) -> list[str]:
    """The plan references that no longer resolve under the working directory."""
    problems = drift.drift_text(text, Path.cwd())
    return indented(f"{reference}  {said}" for reference, said in problems)


def _report(story: issue.Issue | Exception) -> None:
    """Print the plan, the commits already on the branch, and the plan's stale references."""

    def plan_text() -> str:
        return sections.get(usable(story).body, "Plan").strip("\n")

    block("## Plan", lambda: plan_text().splitlines() or ["  none"])
    block("## Commits", _commits_block)
    block("## Plan drift", lambda: _drift_block(plan_text()))


# --- context ----------------------------------------------------------------


def _story(number: int) -> issue.Issue | Exception:
    """The story, or the failure to read it, so both blocks that need it share the one lookup."""
    try:
        return issue.view(gh.repo_slug(), number)
    except Exception as error:
        return error


def _branch_line(settings: Settings | Exception, story: issue.Issue | Exception, number: int) -> str:
    """`Branch: <name> (local|origin|none)`; the name and its location can fail one without the other."""
    try:
        resolved = usable(settings)
        repo = gh.repo_slug()
        kind = fields.get_fields(resolved, repo, number, ("Kind",))["Kind"]
        branch = _branch_name(resolved, kind, usable(story).title, number)
    except Exception as error:  # the blockers and the plan are the blocks the model needs most
        return f"Branch: unavailable ({reason(error)})"
    try:
        local, remote = _exists(branch)
    except Exception as error:  # the name is the half the model acts on
        return f"Branch: {branch} (unknown: {reason(error)})"
    return f"Branch: {branch} ({_where(local, remote)})"


def _agreement(story: issue.Issue | Exception) -> None:
    """The Story and the Scope: what the session judges still holds before the branch is made."""

    def text(name: str) -> list[str]:
        return indented(sections.get(usable(story).body, name, "").splitlines())

    block("## Story", lambda: text("Story"))
    block("## Scope", lambda: text("Scope"))


def context(args: argparse.Namespace) -> int:
    """Print the branch, the blockers, the story and its scope, the plan, the commits, and the drift."""
    settings = settings_or_error()
    story = _story(args.issue)
    print(_branch_line(settings, story, args.issue))
    block("Blockers:", lambda: blockers_block(gh.repo_slug(), args.issue))
    _agreement(story)
    _report(story)
    return 0


# --- apply ------------------------------------------------------------------


def next_line(number: int) -> str:
    """The line the step ends on; the build runs on in the same turn as this, and ends the turn.

    What the person reads at the end of that turn is this line, so it names the one command they
    type when the branch is built rather than the step that has only just started. The branch
    review belongs to finish, which is what that command reaches next.
    """
    return f"Next: /deckhand:next {number} to finish."


def _configure(parser: argparse.ArgumentParser) -> None:
    """`start apply` takes the issue number and nothing else."""


@step("start", _configure)
def apply(args: argparse.Namespace) -> int:
    """Branch a story from origin/main, or check out the branch it already has, and show the plan."""
    repo = gh.repo_slug()
    story = issue.view(repo, args.issue)
    refuse_stub(args.issue, story.body)
    open_blockers = issue.blockers(repo, args.issue)
    if open_blockers:
        raise Refusal("blocked by " + "; ".join(f"#{number} {title}" for number, title in open_blockers))
    settings = config.load()
    values = fields.get_fields(settings, repo, args.issue, ("Status", "Kind"))
    if values["Status"] is None:
        raise Refusal(f"#{args.issue} is not on the board; run /deckhand:next {args.issue}")
    branch = _branch_name(settings, values["Kind"], story.title, args.issue)
    local, remote = _exists(branch)
    if remote:
        _checkout(branch, local)
        print(f"Existing branch {branch}; status unchanged")
    else:
        _start_branch(branch, local)
        print(fields.set_field(settings, repo, args.issue, "Status", "In Progress"))
    # Bookkeeping, so it comes after the status write the step exists to make: whoever started the
    # story owns it, and adding an assignee GitHub already has changes nothing.
    issue.assign(repo, args.issue)
    print("Assigned @me")
    print(f"Issue: {story.url}")
    _report(story)
    print(next_line(args.issue))
    return 0
