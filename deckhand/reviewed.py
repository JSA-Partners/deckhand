"""The clean pass: the commit a branch review passed, kept as a ref and said on the issue.

The sha is computed here rather than handed in, because the session is standing on the commit it just
read and anything it types is a chance to name a different one. `gates` checks the ref before a pull
request opens; the comment is for whoever reads the issue afterwards.

The ref is local to the clone, which is where `gates` runs. A clone that never ran the review holds no
ref even when the issue carries the comment, and the refusal says so.
"""

from __future__ import annotations

import argparse

from deckhand import gh, git, invoke, issue, log
from deckhand.step import Refusal, reason, refuse_git, step

PREFIX = "Reviewed:"


def ref_name(number: int) -> str:
    """The ref an apply for `number` writes, and the one `gates` reads back."""
    return f"refs/deckhand/reviewed/{number}"


def _summary(value: str) -> str:
    """The one line on the pass, its whitespace collapsed to single spaces.

    It follows the sha on the log entry's first line, which the log reader takes as the whole entry,
    so an argument a shell wrapped onto two lines must not carry its newline onto the issue.
    """
    said = " ".join(value.split())
    if not said:
        raise Refusal("the summary needs a line on the pass")
    return said


def context(args: argparse.Namespace) -> int:
    """Print the story, the branch, and the commit an apply would claim as reviewed."""
    try:
        story = issue.view(gh.repo_slug(), args.issue)
        print(f"Title: {story.title}")
        print(f"Issue: {story.url}")
    except Exception as error:
        print(f"Title: unavailable ({reason(error)})")
    try:
        branch = git.run("branch", "--show-current")
        print(f"Branch: {branch or '(detached)'}")
    except Exception as error:
        print(f"Branch: unavailable ({reason(error)})")
    try:
        print(f"Commit: {git.run('rev-parse', '--verify', 'HEAD')}")
    except Exception as error:
        print(f"Commit: unavailable ({reason(error)})")
    print(invoke.apply_line("reviewed", str(args.issue), '"<one line>"'))
    return 0


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("summary", help="one line on the clean pass, posted after the sha")


@step("reviewed", _configure)
def apply(args: argparse.Namespace) -> int:
    """Read HEAD, write the reviewed ref at it, and post the human Reviewed: line on the issue."""
    said = _summary(args.summary)
    head = refuse_git("rev-parse", "--verify", "HEAD")
    name = ref_name(args.issue)
    git.set_ref(name, head)
    print(f"Ref set: {name} -> {head}")
    issue.comment(gh.repo_slug(), args.issue, log.checked(f"{PREFIX} {head} {said}"))
    print("Logged Reviewed")
    return 0
