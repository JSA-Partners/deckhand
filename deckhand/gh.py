from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from deckhand.git import GitError, Runner, call, run, stderr_tail


class GhError(GitError):
    pass


class HeadMoved(GhError):
    pass


@dataclass(frozen=True)
class Checks:
    state: str
    failing: list[str] = field(default_factory=list)


def gh(argv: list[str], runner: Runner, cwd: Path | None = None) -> str:
    try:
        return call(["gh", *argv], cwd, runner)
    except GitError as error:
        raise GhError(error.command, error.code, error.stderr_tail) from None


def pr_for(branch: str, repo: str, runner: Runner = run) -> dict | None:
    out = gh(
        ["pr", "list", "--repo", repo, "--head", branch, "--state", "open",
         "--json", "number,url,isDraft,headRefOid", "--limit", "1"],
        runner,
    )  # fmt: skip
    found = json.loads(out)
    return found[0] if found else None


def ensure_draft_pr(branch: str, repo: str, title: str, body: str, runner: Runner = run) -> dict:
    existing = pr_for(branch, repo, runner)
    if existing:
        return existing
    gh(
        ["pr", "create", "--repo", repo, "--draft", "--head", branch, "--title", title,
         "--body", body],
        runner,
    )  # fmt: skip
    created = pr_for(branch, repo, runner)
    if created is None:
        raise GhError("gh pr", 0, "pull request not found after create")
    return created


def mark_ready(number: int, repo: str, runner: Runner = run) -> None:
    gh(["pr", "ready", str(number), "--repo", repo], runner)


def checks(number: int, repo: str, runner: Runner = run) -> Checks:
    argv = ["gh", "pr", "checks", str(number), "--repo", repo, "--required", "--json", "name,bucket"]
    try:
        done = runner(argv, None, None)
    except GitError as error:
        raise GhError(error.command, error.code, error.stderr_tail) from None
    rows = _rows(done.stdout)
    if rows is None:
        if any(none in done.stderr.lower() for none in ("no required checks", "no checks reported")):
            return Checks("pass")
        raise GhError("gh pr", done.returncode, stderr_tail(done.stderr))
    failing = [r["name"] for r in rows if r["bucket"] in ("fail", "cancel")]
    if failing:
        return Checks("fail", failing)
    if any(r["bucket"] == "pending" for r in rows):
        return Checks("pending")
    return Checks("pass")


def merge_pinned(number: int, repo: str, head: str, runner: Runner = run) -> None:
    try:
        gh(
            ["pr", "merge", str(number), "--repo", repo, "--squash",
             "--match-head-commit", head],
            runner,
        )  # fmt: skip
    except GhError as error:
        if "head branch was modified" in error.stderr_tail.lower():
            raise HeadMoved(error.command, error.code, error.stderr_tail) from None
        raise


def repo_name(clone: Path, runner: Runner = run) -> str:
    out = gh(["repo", "view", "--json", "nameWithOwner"], runner, cwd=clone)
    return json.loads(out)["nameWithOwner"]


def _rows(stdout: str) -> list[dict] | None:
    try:
        rows = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return rows if isinstance(rows, list) else None
