"""What a context tells the session to run next.

A context prints everything the step needs except the command line itself, and a session that has
to recover that from `--help` has spent a call on what the context already knew. These are the two
lines that close that gap: the `apply` a context expects, and the pathspec add for files it listed.
"""

from __future__ import annotations

import shlex


def apply_line(step: str, *parts: str) -> str:
    """The `apply` this context expects, filled from what the context just read.

    A part the session still has to supply is written as `<placeholder>`; a part the context knows
    is filled in, so the line can be run as it is printed.
    """
    return " ".join(["Apply: deckhand", step, "apply", *parts])


def stage_line(paths: list[str]) -> str:
    """The pathspec add for the files a context listed, or nothing when it listed none.

    Quoted per path rather than as one string, so the line runs as printed against a path holding a
    space, and so a reader can drop the files this commit is not about.
    """
    if not paths:
        return ""
    return "Stage: git add -- " + " ".join(shlex.quote(path) for path in paths)
