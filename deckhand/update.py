"""The update step: bring a story's build up to date with main.

Another story merged first, and the repository's rule wants the branch up to date before it can
merge. With a pull request open, GitHub makes the merge commit from main on the server, so the
reviewed commit stays what the person read, the checks run again, and finish never runs a second
time. A conflict is the one case GitHub refuses, and then the branch is merged in the worktree by
hand, logged as a Deviation, and finished again. A story still building has no pull request yet, so
nothing asks GitHub for it: the same merge happens locally instead, in the worktree the branch is
checked out in. Either way a merge only adds a commit; it never rewrites the one the last Reviewed:
entry names, which is why finish accepts it.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from deckhand import gh, git, issue, log, worktree
from deckhand.step import Refusal, local_branch, step, trunk

CONFLICT = (
    "GitHub could not merge main into the branch; merge origin/main in the worktree, resolve, "
    "commit, log a Deviation, and finish again"
)


def _open_pull_request(repo: str, number: int) -> str | None:
    """The URL of the story's open pull request, from the log, or None when it has none."""
    story = issue.view(repo, number)
    entry = log.last(story, "Pull request:")
    return issue.pull_request_state(repo, entry.text.split()[0]) if entry else None


def _branch_here(number: int) -> tuple[str, Path] | None:
    """The story's branch and the worktree holding it, when one is checked out."""
    branch = local_branch(number)
    path = worktree.checked_out(branch) if branch else None
    return (branch, path) if branch and path else None


def _merge_into(branch: str, path: Path) -> None:
    """Merge the trunk into the branch where it is checked out; a conflict is the caller's to resolve."""
    git.run("fetch", "origin", "main", cwd=path)
    try:
        git.run("merge", "--no-edit", trunk(), cwd=path)
    except git.GitError as error:
        raise Refusal(CONFLICT) from error
    print(f"Merged {trunk()} into {branch}")


def context(args: argparse.Namespace) -> int:
    """Print the pull request the update would act on, or the branch when the story has none yet."""
    url = _open_pull_request(gh.repo_slug(), args.issue)
    if url is not None:
        print(f"Pull request: {url}")
        return 0
    here = _branch_here(args.issue)
    print(f"Branch: {here[0]} at {here[1]}" if here else "Branch: none")
    return 0


def _configure(parser: argparse.ArgumentParser) -> None:
    pass


@step("update", _configure)
def apply(args: argparse.Namespace) -> int:
    """Bring the story up to date with main: through its pull request, or its branch when it has none yet."""
    repo = gh.repo_slug()
    url = _open_pull_request(repo, args.issue)
    if url is not None:
        number = url.rstrip("/").rsplit("/", 1)[1]
        try:
            gh.run("api", "-X", "PUT", f"repos/{repo}/pulls/{number}/update-branch")
        except gh.GhError as error:
            raise Refusal(CONFLICT) from error
        print("Updated from main; the checks run again")
        return 0
    here = _branch_here(args.issue)
    if here is None:
        raise Refusal(f"#{args.issue} has no open pull request and no branch checked out here")
    _merge_into(*here)
    return 0
