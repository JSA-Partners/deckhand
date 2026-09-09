"""One comment on a story, posted as it was typed: `deckhand comment N "<text>"`.

The issue's comments are the process's log, and this is how the session writes to it, so nothing in
a skill ever runs `gh` itself. The command prefixes nothing and validates nothing but that there is
something to post: the caller says what kind of comment it is, `Deviation:` and the rest.
"""

from __future__ import annotations

import argparse

from deckhand import gh, issue
from deckhand.cli import command
from deckhand.step import issue_number


def comment_text(value: str) -> str:
    """An argparse `type=` that keeps the text as it was typed and refuses one with nothing in it."""
    if not value.strip():
        raise argparse.ArgumentTypeError("the comment text cannot be blank")
    return value


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("issue", type=issue_number, help="the issue number")
    parser.add_argument("text", type=comment_text, help="the comment, posted as it is written")


@command("comment", _configure)
def post(args: argparse.Namespace) -> int:
    """Post one comment on the issue, exactly as it was written."""
    print(issue.comment(gh.repo_slug(), args.issue, args.text))
    return 0
