from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

TIMEOUT = 120
TAIL_LINES = 5
TOKEN = re.compile(r"(gh[pousr]_|github_pat_|lin_api_|lin_oauth_)\w+")

Runner = Callable[[list[str], Path | None, str | None], subprocess.CompletedProcess[str]]


def run(argv: list[str], cwd: Path | None = None, stdin: str | None = None):
    return subprocess.run(
        argv,
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
    )


class GitError(Exception):
    def __init__(self, command: str, code: int, stderr_tail: str) -> None:
        super().__init__(f"{command} failed with exit code {code}: {stderr_tail}")
        self.command = command
        self.code = code
        self.stderr_tail = stderr_tail


def stderr_tail(stderr: str) -> str:
    lines = stderr.strip().splitlines()[-TAIL_LINES:]
    return TOKEN.sub("[redacted]", "\n".join(lines))


def call(argv: list[str], cwd: Path | None, runner: Runner, stdin: str | None = None) -> str:
    done = runner(argv, cwd, stdin)
    if done.returncode != 0:
        raise GitError(" ".join(argv[:2]), done.returncode, stderr_tail(done.stderr))
    return done.stdout


def worktree_add(clone: Path, branch: str, base: str, runner: Runner = run) -> Path:
    path = clone / ".worktrees" / branch
    if path.exists():
        return path
    known = runner(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], clone, None)
    if known.returncode == 0:
        call(["git", "worktree", "add", str(path), branch], clone, runner)
    else:
        call(["git", "worktree", "add", "-b", branch, str(path), base], clone, runner)
    return path


def head(worktree: Path, runner: Runner = run) -> str:
    return call(["git", "rev-parse", "HEAD"], worktree, runner).strip()


def diff(worktree: Path, base: str, paths: list[str] | None = None, runner: Runner = run) -> str:
    argv = ["git", "diff", f"{base}...HEAD"]
    if paths:
        argv += ["--", *paths]
    return call(argv, worktree, runner)


def changed_files(worktree: Path, base: str, runner: Runner = run) -> list[str]:
    out = call(["git", "diff", "--name-only", f"{base}...HEAD"], worktree, runner)
    return out.splitlines()


def ls_files(repo: Path, ref: str, runner: Runner = run) -> list[str]:
    return call(["git", "ls-tree", "-r", "--name-only", ref], repo, runner).splitlines()


def push(worktree: Path, branch: str, runner: Runner = run) -> None:
    call(["git", "push", "--set-upstream", "origin", branch], worktree, runner)


def apply_hunks(worktree: Path, base: str, patch: str, message: str, runner: Runner = run) -> str:
    call(["git", "checkout", "--detach", base], worktree, runner)
    call(["git", "apply", "--index", "-"], worktree, runner, stdin=patch)
    call(["git", "commit", "-m", message], worktree, runner)
    return head(worktree, runner)
