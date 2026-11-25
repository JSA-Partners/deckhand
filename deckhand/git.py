from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from deckhand import config
from deckhand.step import Refusal, hand_role

TIMEOUT = 120
TAIL_LINES = 5
TOKEN = re.compile(r"(gh[pousr]_|github_pat_|lin_api_|lin_oauth_)\w+")
NO_TERMINAL = "Device not configured"
NO_PROMPT = (
    "git needs a passphrase or password, and its prompt cannot run in the engine or a hand; "
    "load the key into the SSH agent with ssh-add, or use a git credential helper"
)

UNHOOKED = ("-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false")
# Without it, status and worktree diffs run git inside a nested repository a hand configured.
UNNESTED = "--ignore-submodules=all"
GITLINK = "160000"
NESTED = "a commit takes no nested repository: {}; remove it or list it in .gitignore"

CLAUDE_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "TERM", "TMPDIR", "SSH_AUTH_SOCK")
CLAUDE_ENV += ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")
CLAUDE_ENV += ("GIT_SSH_COMMAND", "GIT_SSH", "GIT_CONFIG_GLOBAL", "GIT_ASKPASS", "SSH_ASKPASS")
CLAUDE_ENV += ("NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR", "AWS_REGION", "AWS_PROFILE", "CLOUD_ML_REGION")
CLAUDE_ENV_PREFIXES = ("LC_", "XDG_", "ANTHROPIC_", "CLAUDE_", "DECKHAND_")
# A running Claude Code session sets these for itself; a process started under it would take them as its own.
SESSION_ENV = frozenset(
    {
        "CLAUDE_CODE_BRIDGE_SESSION_ID",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_SESSION_ATTENDED",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_SSE_PORT",
        "CLAUDE_EFFORT",
        "CLAUDE_ENV_FILE",
        "CLAUDE_PID",
        "CLAUDE_PLUGIN_DATA",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PROJECT_DIR",
    }
)
Runner = Callable[[list[str], Path | None, str | None], subprocess.CompletedProcess[str]]


UNTRUSTED = (
    "{} is on no trusted PATH directory; deckhand skips empty, relative, temp, state, clone and worktree entries"
)


# A directory a hand can write must never supply a program deckhand runs outside the sandbox.
def _untrusted_roots(extra: Iterable[Path] = ()) -> list[Path]:
    from deckhand import answers

    roots = [Path(tempfile.gettempdir()), Path("/tmp"), answers.state_dir()]
    if os.environ.get("TMPDIR"):
        roots.append(Path(os.environ["TMPDIR"]))
    roots += [*config.local().clones.values(), *extra]
    return [root.expanduser().resolve() for root in roots]


def _trusted(directory: Path, untrusted: list[Path]) -> bool:
    resolved = directory.resolve()
    return not any(resolved.is_relative_to(root) for root in untrusted)


def trusted_path(extra: Iterable[Path] = ()) -> str:
    untrusted = _untrusted_roots(extra)
    kept: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if os.path.isabs(entry) and entry not in kept and _trusted(Path(entry), untrusted):
            kept.append(entry)
    return os.pathsep.join(kept)


def _program(name: str, search: str) -> str:
    if os.path.isabs(name):
        found = name if _trusted(Path(name).parent, _untrusted_roots()) else None
    else:
        found = shutil.which(name, path=search) if search and os.sep not in name else None
    if found is None:
        raise GitError(name, -1, UNTRUSTED.format(name))
    return found


def _passed(name: str) -> bool:
    return (name in CLAUDE_ENV or name.startswith(CLAUDE_ENV_PREFIXES)) and name not in SESSION_ENV


# A hand inherits the engine's environment, so a claude process gets only what it needs; git may authenticate by it.
def _environment(program: str, search: str) -> dict[str, str]:
    held = {**os.environ, "PATH": search}
    return {k: v for k, v in held.items() if _passed(k)} if program == "claude" else held


def run(
    argv: list[str], cwd: Path | None = None, stdin: str | None = None, alone: bool = False
) -> subprocess.CompletedProcess[str]:
    command = " ".join(argv[:2])
    search = trusted_path()
    program = _program(argv[0], search)
    full = [program, *UNHOOKED, *argv[1:]] if argv[0] == "git" else [program, *argv[1:]]
    try:
        return subprocess.run(
            full,
            env=_environment(argv[0], search),
            # Without a directory, gh and claude would run git in a repository a hand may have planted.
            cwd=cwd or Path.home(),
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT,
            start_new_session=alone,
        )
    except subprocess.TimeoutExpired:
        raise GitError(command, -1, f"timed out after {TIMEOUT} seconds") from None
    except FileNotFoundError:
        raise GitError(command, -1, f"{argv[0]} not found") from None
    except OSError as error:
        raise GitError(command, -1, f"{argv[0]} could not run: {error.strerror or error}") from None


# For a process that owns a terminal: Ctrl-C there never reaches the child, and the child never reads its keys.
def alone(argv: list[str], cwd: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return run(argv, cwd, "" if stdin is None else stdin, alone=True)


class GitError(Exception):
    def __init__(self, command: str, code: int, stderr_tail: str) -> None:
        stderr_tail = NO_PROMPT if NO_TERMINAL in stderr_tail else stderr_tail
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


def worktree_detached(clone: Path, name: str, base: str, runner: Runner = run) -> Path:
    path = worktree_path(clone, name)
    if not path.exists():
        call(["git", "worktree", "add", "--detach", str(path), base], clone, runner)
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


# A base merged in since the review is taken out, so the diff holds only the story's own change.
def changed_since(worktree: Path, reviewed: str, base: str, runner: Runner = run) -> str:
    if is_ancestor(worktree, base, reviewed, runner):
        return diff_since(worktree, reviewed, runner)
    tree = call(["git", "merge-tree", "--write-tree", reviewed, base], worktree, runner).split("\n", 1)[0]
    return call(["git", "diff", tree, "HEAD"], worktree, runner)


def changed_files(worktree: Path, base: str, runner: Runner = run) -> list[str]:
    out = call(["git", "diff", "--name-only", f"{base}...HEAD"], worktree, runner)
    return out.splitlines()


def show(repo: Path, ref: str, path: str, runner: Runner = run) -> str:
    return call(["git", "show", f"{ref}:{path}"], repo, runner)


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
    # A second commit must never stage onto a gitlink the first has staged and not yet removed.
    with _locked(worktree, runner):
        _refuse_strays(worktree, runner)
        try:
            _stage(worktree, runner)
        finally:
            _refuse_strays(worktree, runner)
        call(["git", "commit", "-F", "-"], worktree, runner, stdin=message)


def _stage(worktree: Path, runner: Runner) -> None:
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
    links = {path for path, meta in _entries(["ls-files", "-s"], worktree, runner) if meta.startswith(GITLINK)}
    # A pathspec that reaches an index gitlink makes git add run status inside that repository.
    chosen = [path for path in paths if not any(_overlaps(path, link) for link in links)]
    if chosen:
        pathspecs = "".join(f":(literal){path}\0" for path in chosen)
        call(["git", "add", "--pathspec-from-file=-", "--pathspec-file-nul"], worktree, runner, stdin=pathspecs)


def _overlaps(path: str, link: str) -> bool:
    return path == link or path.startswith(f"{link}/") or link.startswith(f"{path}/")


def _refuse_strays(worktree: Path, runner: Runner) -> None:
    head = dict(_entries(["ls-tree", "-r", "HEAD"], worktree, runner))
    known = {(path, meta.split()[2]) for path, meta in head.items() if meta.startswith(GITLINK)}
    if merging(worktree, runner):
        merged = _entries(["ls-tree", "-r", "MERGE_HEAD"], worktree, runner)
        known |= {(path, meta.split()[2]) for path, meta in merged if meta.startswith(GITLINK)}
    strays = {}
    for path, meta in _entries(["ls-files", "-s"], worktree, runner):
        mode, sha, stage = meta.split()
        if mode == GITLINK and stage == "0" and (path, sha) not in known:
            strays[path] = sha
    if not strays:
        return
    lines = []
    for path, sha in sorted(strays.items()):
        lines.append(f"0 {'0' * len(sha)}\t{path}")
        if path in head:
            mode, _, blob = head[path].split()
            lines.append(f"{mode} {blob} 0\t{path}")
    call(["git", "update-index", "-z", "--index-info"], worktree, runner, stdin="".join(f"{line}\0" for line in lines))
    raise Refusal(NESTED.format(", ".join(sorted(strays))))


def _entries(argv: list[str], worktree: Path, runner: Runner) -> list[tuple[str, str]]:
    records = call(["git", argv[0], "-z", *argv[1:]], worktree, runner).split("\0")
    return [(path, meta) for meta, _, path in (record.partition("\t") for record in records) if path]


# Commits alone take it: a merge stages no hand pathspec, and git's index.lock already guards it.
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


def abort_merge(worktree: Path, runner: Runner = run) -> None:
    call(["git", "merge", "--abort"], worktree, runner)


def fetch(clone: Path, runner: Runner = run) -> None:
    call(["git", "fetch", "origin", "--prune"], clone, runner)


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
