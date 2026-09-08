"""Every git call the process makes, and one reading of what git says when a call fails.

git writes a paragraph where a caller wants a sentence: progress lines and advice hints come before
the line that says what went wrong, and the line that does say it often ends in a colon with the
detail indented under it. `run` keeps that one line, detail folded in, so a refusal reads as git's
own words without the paragraph around them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

MARKERS = ("fatal:", "error:")
REJECTED = "! [rejected]"


class GitError(Exception):
    """git exited non-zero; the message is the line of its stderr that says why."""


def message(stderr: str) -> str:
    """The line of `stderr` that says what went wrong, with any indented detail folded into it."""
    lines = stderr.splitlines()
    # A rejected push says both that it failed and why; the `error:` summary under it says only that
    # some refs did not go, so the parenthetical on this line is the whole answer.
    rejected = next((line for line in lines if REJECTED in line), None)
    if rejected is not None:
        return " ".join(rejected.split())
    index = next((i for i, line in enumerate(lines) if line.strip().startswith(MARKERS)), None)
    if index is None:
        index = next((i for i, line in enumerate(lines) if line.strip()), None)
    if index is None:
        return ""
    head = lines[index].strip()
    if not head.endswith(":"):
        return head
    detail = []
    for line in lines[index + 1 :]:
        if not line[:1].isspace():  # the indent is what marks a line as belonging to the head
            break
        if line.strip():
            detail.append(line.strip())
    return f"{head} {', '.join(detail)}" if detail else head


def run(*args: str, cwd: Path | None = None) -> str:
    """git's stdout without the newline it ends with; a non-zero exit raises `GitError`.

    Decoded leniently, and stripped of newlines rather than of whitespace: a diff is not always
    valid UTF-8, and a carriage return at the end of one is content, not padding.
    """
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except FileNotFoundError as error:
        raise GitError("git is not installed or not on PATH") from error
    if result.returncode != 0:
        said = message(result.stderr.decode("utf-8", errors="replace"))
        raise GitError(said or f"git {' '.join(args)} failed")
    return result.stdout.decode("utf-8", errors="replace").strip("\n")
