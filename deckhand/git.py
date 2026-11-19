from __future__ import annotations

import fcntl
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from deckhand import config
from deckhand.step import Refusal, hand_role

TIMEOUT = 120
TAIL_LINES = 5
TOKEN = re.compile(r"(gh[pousr]_|github_pat_|lin_api_|lin_oauth_)\w+")

UNHOOKED = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
# Without it, status and worktree diffs run git inside a nested repository a hand configured.
UNNESTED = "--ignore-submodules=all"
GITLINK = "160000"
NESTED = "a commit takes no nested repository: {}; remove it or list it in .gitignore"

Runner = Callable[[list[str], Path | None, str | None], subprocess.CompletedProcess[str]]


def run(argv: list[str], cwd: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    command = " ".join(argv[:2])
    full = [argv[0], *UNHOOKED, *argv[1:]] if argv[0] == "git" else argv
    try:
        return subprocess.run(
            full,
            # Without a directory, gh and claude would run git in a repository a hand may have planted.
            cwd=cwd or Path.home(),
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GitError(command, -1, f"timed out after {TIMEOUT} seconds") from None
    except FileNotFoundError:
        raise GitError(command, -1, f"{argv[0]} not found") from None
    except OSError as error:
        raise GitError(command, -1, f"{argv[0]} could not run: {error.strerror or error}") from None


class GitError(Exception):
    def __init__(self, command: str, code: int, stderr_tail: str) -> None:
        super().__init__(f"{command} failed with exit code {code}: {stderr_tail}")
        self.command = command
        self.code = code
        self.stderr_tail = stderr_tail


def stderr_tail(stderr: str, count: int = TAIL_LINES) -> str:
    lines = stderr.strip().splitlines()[-count:]
    return TOKEN.sub("[redacted]", "\n".join(lines))


def call(argv: list[str], cwd: Path | None, runner: Runner, stdin: str | None = None) -> str:
    done = runner(argv, cwd, stdin)
    if done.returncode != 0:
        raise GitError(" ".join(argv[:2]), done.returncode, stderr_tail(done.stderr))
    return done.stdout


def working_directory(runner: Runner = run) -> Path:
    cwd = Path.cwd()
    if not hand_role():
        return cwd
    for clone in config.local().clones.values():
        listing = runner(["git", "worktree", "list", "--porcelain"], clone, None)
        listed = [
            Path(line[len("worktree ") :]) for line in listing.stdout.splitlines() if line.startswith("worktree ")
        ]
        if listing.returncode == 0 and any(path.is_dir() and path.samefile(cwd) for path in listed):
            return cwd
    raise Refusal(f"a hand runs deckhand only from its story worktree, not {cwd}")


def worktree_path(clone: Path, branch: str) -> Path:
    return clone / ".worktrees" / branch


def worktree_add(clone: Path, branch: str, base: str, runner: Runner = run) -> Path:
    path = worktree_path(clone, branch)
    if path.exists():
        return path
    known = runner(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], clone, None)
    if known.returncode == 0:
        call(["git", "worktree", "add", str(path), branch], clone, runner)
    else:
        call(["git", "worktree", "add", "-b", branch, str(path), base], clone, runner)
    return path


def clone_root(cwd: Path, runner: Runner = run) -> Path:
    try:
        common = call(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd, runner).strip()
    except GitError:
        raise Refusal(f"{cwd} is not in a git repository; run deckhand from the clone or a story worktree") from None
    return Path(common).parent


def head(worktree: Path, runner: Runner = run) -> str:
    return call(["git", "rev-parse", "HEAD"], worktree, runner).strip()


def dirty(worktree: Path, runner: Runner = run) -> bool:
    return bool(call(["git", "status", "--porcelain", UNNESTED], worktree, runner).strip())


def diff(worktree: Path, base: str, paths: list[str] | None = None, runner: Runner = run) -> str:
    argv = ["git", "diff", f"{base}...HEAD"]
    if paths:
        argv += ["--", *paths]
    return call(argv, worktree, runner)


def diff_since(worktree: Path, commit: str, runner: Runner = run) -> str:
    return call(["git", "diff", f"{commit}..HEAD"], worktree, runner)


def changed_files(worktree: Path, base: str, runner: Runner = run) -> list[str]:
    out = call(["git", "diff", "--name-only", f"{base}...HEAD"], worktree, runner)
    return out.splitlines()


def ls_files(repo: Path, ref: str, runner: Runner = run) -> list[str]:
    return call(["git", "ls-tree", "-r", "--name-only", ref], repo, runner).splitlines()


def merge_base(repo: Path, a: str, b: str, runner: Runner = run) -> str:
    return call(["git", "merge-base", a, b], repo, runner).strip()


def is_ancestor(repo: Path, ancestor: str, descendant: str, runner: Runner = run) -> bool:
    return runner(["git", "merge-base", "--is-ancestor", ancestor, descendant], repo, None).returncode == 0


def unmerged(worktree: Path, runner: Runner = run) -> list[str]:
    return call(["git", "diff", "--name-only", "--diff-filter=U", UNNESTED], worktree, runner).splitlines()


def merging(worktree: Path, runner: Runner = run) -> bool:
    return runner(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], worktree, None).returncode == 0


def commit_all(worktree: Path, message: str, runner: Runner = run) -> None:
    # A second commit must never stage while the first holds a gitlink it is about to refuse.
    with _locked(worktree, runner):
        before = _index(worktree, runner)
        _stage(worktree, [path for path, entries in before.items() if _links(entries)], runner)
        after = _index(worktree, runner)
        added = sorted(path for path, entries in after.items() if _links(entries) and entries != before.get(path))
        if added:
            _restore(worktree, added, before, after, runner)
            raise Refusal(NESTED.format(", ".join(added)))
        call(["git", "commit", "-F", "-"], worktree, runner, stdin=message)


def _stage(worktree: Path, links: list[str], runner: Runner) -> None:
    argv = ["git", "status", "--porcelain=v2", "-z", "--no-renames", "--untracked-files=all", UNNESTED]
    paths, nested = [], []
    for record in call(argv, worktree, runner).split("\0"):
        if record.startswith("1 "):
            paths.append(record.split(" ", 8)[8])
        elif record.startswith("u "):
            paths.append(record.split(" ", 10)[10])
        elif record.startswith("? "):
            (nested if record.endswith("/") else paths).append(record[2:].rstrip("/"))
    if nested:
        raise Refusal(NESTED.format(", ".join(nested)))
    # A pathspec that reaches an index gitlink makes git add run status inside that repository.
    chosen = [path for path in paths if not any(_overlaps(path, link) for link in links)]
    if chosen:
        pathspecs = "".join(f":(literal){path}\0" for path in chosen)
        call(["git", "add", "--pathspec-from-file=-", "--pathspec-file-nul"], worktree, runner, stdin=pathspecs)


def _overlaps(path: str, link: str) -> bool:
    return path == link or path.startswith(f"{link}/") or link.startswith(f"{path}/")


def _index(worktree: Path, runner: Runner) -> dict[str, list[str]]:
    entries: dict[str, list[str]] = {}
    for record in call(["git", "ls-files", "-s", "-z"], worktree, runner).split("\0"):
        meta, _, path = record.partition("\t")
        if path:
            entries.setdefault(path, []).append(meta)
    return entries


def _links(entries: list[str]) -> bool:
    return any(meta.startswith(f"{GITLINK} ") for meta in entries)


def _restore(
    worktree: Path, paths: list[str], before: dict[str, list[str]], after: dict[str, list[str]], runner: Runner
) -> None:
    lines = []
    for path in paths:
        zero = "0" * len(after[path][0].split()[1])
        lines += [f"0 {zero}\t{path}", *(f"{meta}\t{path}" for meta in before.get(path, []))]
    call(["git", "update-index", "-z", "--index-info"], worktree, runner, stdin="".join(f"{line}\0" for line in lines))


@contextmanager
def _locked(worktree: Path, runner: Runner) -> Iterator[None]:
    gitdir = Path(call(["git", "rev-parse", "--path-format=absolute", "--git-dir"], worktree, runner).strip())
    with (gitdir / "deckhand-commit.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def merge_from(worktree: Path, base: str, runner: Runner = run) -> list[str]:
    call(["git", "fetch", "--no-recurse-submodules", "origin"], worktree, runner)
    done = runner(["git", "merge", "--no-edit", base], worktree, None)
    if done.returncode == 0:
        return []
    conflicts = unmerged(worktree, runner)
    if not conflicts:
        raise GitError("git merge", done.returncode, stderr_tail(done.stderr))
    return conflicts


def push(worktree: Path, branch: str, runner: Runner = run) -> None:
    call(["git", "push", "--set-upstream", "origin", branch], worktree, runner)


def apply_hunks(clone: Path, base: str, patch: str, message: str, ref: str, runner: Runner = run) -> str:
    scratch = Path(tempfile.mkdtemp(prefix="deckhand-"))
    try:
        call(["git", "worktree", "add", "--detach", str(scratch), base], clone, runner)
        call(["git", "apply", "-"], scratch, runner, stdin=patch)
        call(["git", "add", "-A"], scratch, runner)
        call(["git", "commit", "-m", message], scratch, runner)
        sha = head(scratch, runner)
        call(["git", "update-ref", ref, sha], clone, runner)
        return sha
    finally:
        runner(["git", "worktree", "remove", "--force", str(scratch)], clone, None)
        shutil.rmtree(scratch, ignore_errors=True)
