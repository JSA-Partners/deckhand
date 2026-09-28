"""Every git call the process makes, and one reading of what git says when a call fails.

git writes a paragraph where a caller wants a sentence: progress lines and advice hints come before
the line that says what went wrong, and the line that does say it often ends in a colon with the
detail indented under it. `run` keeps that one line, detail folded in, so a refusal reads as git's
own words without the paragraph around them.
"""

from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Iterator
from pathlib import Path

MARKERS = ("fatal:", "error:")
REJECTED = "! [rejected]"
NO_REPO = "not a git repository"


class GitError(Exception):
    """git exited non-zero; the message is the line of its stderr that says why, with both streams kept.

    A hook runs inside the push and writes wherever it likes, so the output it failed with may be on
    either stream.
    """

    def __init__(self, message: str, stderr: str = "", stdout: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr
        self.stdout = stdout


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


# The subcommands that only report. Anything else may change what a later read would say, so it
# empties the block rather than being served from it.
READS = frozenset(
    {"rev-parse", "rev-list", "merge-base", "log", "diff", "status", "ls-files", "check-ignore", "show", "for-each-ref"}
)
# The two that report in one form and write in another, so the form decides.
READ_FORMS = frozenset({("worktree", "list"), ("branch", "--show-current")})

_cache: dict[tuple[tuple[str, ...], str | None], str] | None = None
_fetched: set[tuple[tuple[str, ...], str | None]] | None = None


def reads(args: tuple[str, ...]) -> bool:
    """Whether this call only reports; a subcommand nobody listed is a write, which is the safe answer."""
    if not args:
        return False
    return args[0] in READS or (len(args) > 1 and (args[0], args[1]) in READ_FORMS)


@contextlib.contextmanager
def cached() -> Iterator[None]:
    """Memoise every read for the length of the block, and fetch each ref at most once inside it.

    A context reads far more than it writes, but unlike `gh` it does write: `next` sweeps the
    worktrees of closed stories while it briefs. So a call that is not a read empties the block
    rather than being served from it, and a second fetch of a ref already fetched here is skipped
    instead, because repeating it inside one command cannot tell the caller anything new.
    """
    global _cache, _fetched
    if _cache is not None:  # already inside a block; the outer one owns the cache
        yield
        return
    _cache, _fetched = {}, set()
    try:
        yield
    finally:
        _cache, _fetched = None, None


def run(*args: str, cwd: Path | None = None) -> str:
    """git's stdout without the newline it ends with; a non-zero exit raises `GitError`.

    Decoded leniently, and stripped of newlines rather than of whitespace: a diff is not always
    valid UTF-8, and a carriage return at the end of one is content, not padding.
    """
    key = (args, str(cwd) if cwd is not None else None)
    if _cache is not None and _fetched is not None:
        if args[:1] == ("fetch",) and key in _fetched:
            return ""
        if reads(args):
            if key in _cache:
                return _cache[key]
        else:
            _cache.clear()
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except FileNotFoundError as error:
        raise GitError("git is not installed or not on PATH") from error
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        stdout = result.stdout.decode("utf-8", errors="replace")
        if NO_REPO in stderr:
            # git names the directory it searched from; what a caller needs is where it ran and that
            # deckhand wanted a repository, because the usual cause is a step run from the cache.
            where = cwd or Path.cwd()
            raise GitError(f"not inside a git repository: {where}; run deckhand from the clone", stderr, stdout)
        raise GitError(message(stderr) or f"git {' '.join(args)} failed", stderr, stdout)
    out = result.stdout.decode("utf-8", errors="replace").strip("\n")
    if _cache is not None and _fetched is not None:
        if reads(args):
            _cache[key] = out
        elif args[:1] == ("fetch",):
            _fetched.add(key)
    return out


def set_ref(name: str, sha: str) -> None:
    """Point `name` at `sha`; the ref is this clone's own note and never leaves it."""
    run("update-ref", name, sha)


def ref(name: str) -> str | None:
    """The sha `name` points at, or None when this clone does not hold it."""
    try:
        return run("rev-parse", "--verify", f"{name}^{{commit}}")
    except GitError:
        return None
