"""Every story's worktree: where its branch is checked out, making one, and removing a merged one.

The clone stays on main and each story builds in a worktree of its own under `.claude/worktrees/`,
the directory Claude Code's own worktree tool uses, so several sessions can run several stories from
one clone. git is the only record of them: `git worktree list` says where a branch is checked out,
and nothing about a worktree reaches GitHub. A merged story's worktree, branch, and tracking ref
are removed by the first run that can do it from outside the worktree.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from deckhand import git, issue
from deckhand.step import MAIN, ORIGIN_MAIN, reason

WORKTREES = Path(".claude") / "worktrees"
EXCLUDE = f"{WORKTREES.as_posix()}/"
INCLUDE = ".worktreeinclude"
STORY_BRANCH = re.compile(r"^[^/]+/(\d+)-")  # <kind>/<number>-<slug>, the shape start cuts


def _common_dir() -> Path:
    return Path(git.run("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()


def clone_root() -> Path:
    """The main worktree's root, from the clone or from any worktree linked to it."""
    return _common_dir().parent


def from_clone() -> bool:
    """True in the main worktree, the one place a story's worktree can be removed from."""
    git_dir = Path(git.run("rev-parse", "--path-format=absolute", "--git-dir")).resolve()
    return git_dir == _common_dir()


def path_for(branch: str) -> Path:
    """Where a story's worktree lives: the branch's name under the clone's `.claude/worktrees/`."""
    return clone_root() / WORKTREES / branch


def _worktrees() -> dict[str, Path]:
    """`{branch: path}` for every worktree on a branch, after pruning the ones deleted by hand."""
    git.run("worktree", "prune")
    found: dict[str, Path] = {}
    path: Path | None = None
    for line in git.run("worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :])
        elif line.startswith("branch refs/heads/") and path is not None:
            found[line[len("branch refs/heads/") :]] = path
    return found


def checked_out(branch: str) -> Path | None:
    """The worktree that has `branch` checked out, or None when no worktree has."""
    return _worktrees().get(branch)


def here(path: Path) -> bool:
    """True when `path` is the worktree the current directory belongs to."""
    top = Path(git.run("rev-parse", "--show-toplevel")).resolve()
    return path.resolve() == top


def _exclude() -> None:
    """Keep the worktrees out of the clone's status through `.git/info/exclude`, never a commit."""
    root = clone_root()
    (root / WORKTREES).mkdir(parents=True, exist_ok=True)
    try:
        git.run("check-ignore", "-q", WORKTREES.as_posix(), cwd=root)
        return
    except git.GitError:
        pass  # exit 1 is "not ignored", which is the answer that needs the line
    exclude = _common_dir() / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as file:
        file.write(f"{EXCLUDE}\n")


def add(branch: str, new: bool) -> Path:
    """A worktree for `branch` at its path: on a new branch from origin/main when `new`, else on the branch as it is."""
    path = path_for(branch)
    _exclude()
    args = ["-b", branch, ORIGIN_MAIN] if new else [branch]
    git.run("worktree", "add", str(path), *args)
    return path


def include(path: Path) -> list[str]:
    """Copy into the worktree at `path` every file the clone's `.worktreeinclude` names and git ignores.

    Claude Code reads this file for the worktrees it makes itself, and a worktree made with git gets
    none of it, so the same rule is applied here: a file both matched and ignored, never a tracked
    one. git does the matching, `--exclude-from` for the patterns and `check-ignore` for the rule
    that the file is ignored in the first place.
    """
    root = clone_root()
    if not (root / INCLUDE).is_file():
        return []
    listed = [
        name
        for name in git.run("ls-files", "--others", f"--exclude-from={INCLUDE}", "--ignored", cwd=root).splitlines()
        if name.strip()
    ]
    if not listed:
        return []
    try:
        ignored = [name for name in git.run("check-ignore", *listed, cwd=root).splitlines() if name.strip()]
    except git.GitError:
        return []  # exit 1 is "none of them is ignored", which is an answer and not a failure
    for name in ignored:
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    return ignored


def remove(branch: str, path: Path) -> None:
    """Remove the worktree, then its branch, then the tracking ref; git's refusal of a dirty one stands.

    A squash merge leaves nothing `-d` would recognize as merged, so the branch goes with `-D`
    once the worktree is gone; a tracking ref origin already pruned is nothing to report.
    """
    git.run("worktree", "remove", str(path))
    git.run("branch", "-D", branch)
    try:
        git.run("branch", "-dr", f"origin/{branch}")
    except git.GitError:
        pass


def sweep(repo: str, exclude: int | None = None) -> list[str]:
    """From the clone, remove every story worktree whose issue is closed; one line per removal or refusal.

    Only worktrees under the clone's `.claude/worktrees/` on a story branch are deckhand's; story
    `exclude` is the one being briefed, whose worktree is the done row's to remove. From inside any
    worktree nothing is removed, because the one the session stands in may be among them.
    """
    try:
        if not from_clone():
            return []
    except git.GitError:
        return []  # not a repository: nothing to sweep, and the command that called is the one to say so
    root = clone_root() / WORKTREES
    lines: list[str] = []
    for branch, path in _worktrees().items():
        match = STORY_BRANCH.match(branch)
        if not match or root not in path.resolve().parents:
            continue
        number = int(match.group(1))
        if number == exclude:
            continue
        try:
            if issue.view(repo, number).state.upper() != "CLOSED":
                continue
            remove(branch, path)
            lines.append(f"Removed worktree {path}")
        except Exception as error:
            lines.append(f"Left worktree {path}: {reason(error)}")
    return lines


def catch_up() -> list[str]:
    """Fast-forward the clone's main to origin's, so what a session reads in the clone has landed.

    Every range is read against origin/main, but files are read from the clone's working tree, which
    nothing else moves, so a clone left behind drafts stories against code that has since changed.
    `--ff-only` refuses a main that has diverged and a merge that would overwrite an uncommitted
    change, so nothing is lost. A fetch that fails is swallowed, as `next` swallows its own, since
    the ref the clone already holds still answers. Only a catch-up or a refusal is worth a line.
    """
    try:
        root = clone_root()
        if git.run("rev-parse", "--abbrev-ref", "HEAD", cwd=root) != MAIN:
            return []
    except git.GitError:
        return []
    try:
        git.run("fetch", "origin", MAIN, cwd=root)
    except git.GitError:
        pass
    try:
        behind = int(git.run("rev-list", "--count", f"{MAIN}..{ORIGIN_MAIN}", cwd=root))
    except git.GitError:
        return []  # no origin/main to be behind
    if not behind:
        return []
    commits = f"{behind} commit{'' if behind == 1 else 's'}"
    try:
        git.run("merge", "--ff-only", ORIGIN_MAIN, cwd=root)
    except git.GitError as error:
        # git's reason goes last, as the sweep puts it, so git's own full stop ends the line.
        return [
            f"Main is {commits} behind {ORIGIN_MAIN} and could not catch up, "
            f"so files read here are stale: {reason(error)}"
        ]
    return [f"Caught up: {MAIN} moved {commits} to {ORIGIN_MAIN}."]
