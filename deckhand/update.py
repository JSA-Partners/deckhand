"""The update step: a pull request that fell behind main is brought up to date by GitHub.

Another story merged first, and the repository's rule wants the branch up to date before it can
merge. GitHub makes the merge commit from main on the server, so the reviewed commit stays what
the person read, the checks run again, and finish never runs a second time. A conflict is the one
case GitHub refuses, and then the branch is merged in the worktree by hand, logged as a Deviation,
and finished again.
"""

from __future__ import annotations

import argparse

from deckhand import gh, issue, log
from deckhand.step import Refusal, step

CONFLICT = (
    "GitHub could not merge main into the branch; merge origin/main in the worktree, resolve, "
    "commit, log a Deviation, and finish again"
)


def _open_pull_request(repo: str, number: int) -> str:
    """The URL of the story's open pull request, from the log; a story without one is a refusal."""
    story = issue.view(repo, number)
    entry = log.last(story, "Pull request:")
    url = issue.pull_request_state(repo, entry.text.split()[0]) if entry else None
    if not url:
        raise Refusal(f"#{number} has no open pull request")
    return url


def context(args: argparse.Namespace) -> int:
    """Print the pull request the update would act on."""
    print(f"Pull request: {_open_pull_request(gh.repo_slug(), args.issue)}")
    return 0


def _configure(parser: argparse.ArgumentParser) -> None:
    pass


@step("update", _configure)
def apply(args: argparse.Namespace) -> int:
    """Ask GitHub to merge main into the pull request branch of story N."""
    repo = gh.repo_slug()
    url = _open_pull_request(repo, args.issue)
    number = url.rstrip("/").rsplit("/", 1)[1]
    try:
        gh.run("api", "-X", "PUT", f"repos/{repo}/pulls/{number}/update-branch")
    except gh.GhError as error:
        raise Refusal(CONFLICT) from error
    print("Updated from main; the checks run again")
    return 0
