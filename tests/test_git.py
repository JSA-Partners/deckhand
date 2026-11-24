from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from deckhand import git
from deckhand.step import Refusal


class Fake:
    def __init__(self, *replies: tuple[int, str, str]) -> None:
        self.calls: list[tuple[list[str], Path | None, str | None]] = []
        self.replies = list(replies)

    def __call__(self, argv, cwd=None, stdin=None):
        self.calls.append((argv, cwd, stdin))
        code, out, err = self.replies.pop(0) if self.replies else (0, "", "")
        return subprocess.CompletedProcess(argv, code, out, err)

    @property
    def argvs(self) -> list[list[str]]:
        return [c[0] for c in self.calls]


WT = Path("/clone/.worktrees/b")


def test_head_reads_rev_parse() -> None:
    fake = Fake((0, "abc123\n", ""))
    assert git.head(WT, fake) == "abc123"
    assert fake.calls == [(["git", "rev-parse", "HEAD"], WT, None)]


def test_dirty_reads_porcelain_status() -> None:
    fake = Fake((0, " M a.py\n", ""), (0, "", ""))
    assert git.dirty(WT, fake) is True
    assert git.dirty(WT, fake) is False
    assert fake.argvs[0] == ["git", "status", "--porcelain", "--ignore-submodules=all"]


def test_diff_with_and_without_paths() -> None:
    fake = Fake((0, "d1", ""), (0, "d2", ""))
    assert git.diff(WT, "main", runner=fake) == "d1"
    assert git.diff(WT, "main", ["a.py"], runner=fake) == "d2"
    assert fake.argvs == [
        ["git", "diff", "main...HEAD"],
        ["git", "diff", "main...HEAD", "--", "a.py"],
    ]


def test_changed_files_and_ls_files_split_lines() -> None:
    fake = Fake((0, "a.py\nb.py\n", ""), (0, "x\ny\n", ""))
    assert git.changed_files(WT, "main", fake) == ["a.py", "b.py"]
    assert git.ls_files(Path("/clone"), "main", fake) == ["x", "y"]
    assert fake.argvs == [
        ["git", "diff", "--name-only", "main...HEAD"],
        ["git", "ls-tree", "-r", "--name-only", "main"],
    ]


def test_push_sets_upstream() -> None:
    fake = Fake()
    git.push(WT, "b", fake)
    assert fake.calls == [(["git", "push", "--set-upstream", "origin", "b"], WT, None)]


def repo_with_story(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("NAME", "EMAIL"):
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", "t" if key == "NAME" else "t@t")
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", "t" if key == "NAME" else "t@t")
    clone = tmp_path / "clone"
    clone.mkdir()

    def git_(*args: str) -> None:
        subprocess.run(["git", *args], cwd=clone, check=True, capture_output=True)

    git_("init", "-q", "-b", "main")
    (clone / "a.txt").write_text("one\n")
    git_("add", "a.txt")
    git_("commit", "-q", "-m", "base")
    return clone


def test_apply_hunks_builds_in_a_scratch_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clone = repo_with_story(tmp_path, monkeypatch)
    story = git.worktree_add(clone, "story", "main")
    (story / "b.txt").write_text("extra\n")
    subprocess.run(["git", "add", "b.txt"], cwd=story, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "story"], cwd=story, check=True)
    before = git.head(story)
    patch = "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1,2 @@\n one\n+two\n"
    sha = git.apply_hunks(clone, "main", patch, "scratch", "refs/deckhand/reviewed/x")

    def out(*args: str, cwd: Path = clone) -> str:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

    assert git.head(story) == before
    assert out("branch", "--show-current", cwd=story) == "story"
    assert out("rev-parse", "refs/deckhand/reviewed/x") == sha
    assert out("diff", "--name-only", "main", sha) == "a.txt"
    assert out("show", f"{sha}:a.txt") == "one\ntwo"
    assert out("worktree", "list", "--porcelain").count("worktree ") == 2


def reviewed_story(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sibling: str = "s.txt") -> tuple[Path, Path, str]:
    clone = repo_with_story(tmp_path, monkeypatch)
    story = git.worktree_add(clone, "story", "main")

    def commit(where: Path, name: str, text: str) -> None:
        (where / name).write_text(text)
        subprocess.run(["git", "add", name], cwd=where, check=True)
        subprocess.run(["git", "commit", "-q", "-m", name], cwd=where, check=True)

    commit(story, "b.txt", "one\ntwo\n")
    reviewed = git.head(story)
    commit(clone, sibling, "sibling\n")
    subprocess.run(["git", "merge", "-q", "--no-edit", "-X", "ours", "main"], cwd=story, check=True)
    commit(story, "b.txt", "one\nthree\n")
    return clone, story, reviewed


def test_changed_since_leaves_out_a_merged_sibling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, story, reviewed = reviewed_story(tmp_path, monkeypatch)
    changed = git.changed_since(story, reviewed, "main")
    assert "+three" in changed and "-two" in changed
    assert "s.txt" not in changed


def test_changed_since_without_a_new_base_is_the_plain_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clone, story, reviewed = reviewed_story(tmp_path, monkeypatch)
    subprocess.run(["git", "merge", "-q", "--no-edit", "story"], cwd=clone, check=True)
    synced = git.head(story)
    assert git.changed_since(story, synced, "main") == git.diff_since(story, synced)
    assert git.changed_since(story, reviewed, git.merge_base(story, reviewed, "main")) == git.diff_since(
        story, reviewed
    )


def test_changed_since_a_conflicting_base_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, story, reviewed = reviewed_story(tmp_path, monkeypatch, sibling="b.txt")
    with pytest.raises(git.GitError, match="merge-tree"):
        git.changed_since(story, reviewed, "main")


def test_apply_hunks_removes_the_scratch_worktree_when_apply_fails() -> None:
    fake = Fake((0, "", ""), (1, "", "error: patch failed"), (0, "", ""))
    with pytest.raises(git.GitError):
        git.apply_hunks(Path("/clone"), "main", "bad", "m", "refs/x", fake)
    assert fake.argvs[0][:5] == ["git", "worktree", "add", "--detach", fake.argvs[0][4]]
    assert fake.argvs[-1][:4] == ["git", "worktree", "remove", "--force"]
    assert fake.argvs[-1][4] == fake.argvs[0][4]
    assert not any(a[:2] == ["git", "update-ref"] for a in fake.argvs)


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired(["git", "push"], 120), FileNotFoundError("git"), PermissionError("git")]
)
def test_default_runner_failures_become_git_error(error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(git.GitError) as caught:
        git.head(WT)
    assert caught.value.command == "git rev-parse"


def test_failure_raises_git_error_with_tail() -> None:
    fake = Fake((128, "", "l1\nl2\nl3\nl4\nl5\nl6\nl7\n"))
    with pytest.raises(git.GitError) as caught:
        git.head(WT, fake)
    assert caught.value.code == 128
    assert caught.value.stderr_tail == "l3\nl4\nl5\nl6\nl7"


@pytest.mark.parametrize(
    "token",
    ["ghp_abcDEF123456", "gho_abc123", "github_pat_11AAA_bbb222", "lin_api_Zz09Zz09"],
)
def test_git_error_carries_no_secret(token: str) -> None:
    fake = Fake((128, "", f"fatal: https://x:{token}@github.com/a/b denied"))
    with pytest.raises(git.GitError) as caught:
        git.push(WT, "b", fake)
    assert token not in str(caught.value)
    assert token not in caught.value.stderr_tail
    assert token not in caught.value.command


def test_worktree_add_twice_is_one_worktree(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"],
    ):
        subprocess.run(argv, cwd=clone, check=True)
    first = git.worktree_add(clone, "story", "main")
    second = git.worktree_add(clone, "story", "main")
    assert first == second == clone / ".worktrees" / "story"
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=clone, capture_output=True, text=True
    ).stdout
    assert listing.count("worktree ") == 2


def test_worktree_add_reuses_an_existing_branch() -> None:
    fake = Fake((0, "", ""), (0, "", ""))
    git.worktree_add(Path("/nowhere"), "b", "main", fake)
    assert fake.argvs[1] == ["git", "worktree", "add", "/nowhere/.worktrees/b", "b"]


def test_worktree_add_creates_a_new_branch() -> None:
    fake = Fake((1, "", ""), (0, "", ""))
    git.worktree_add(Path("/nowhere"), "b", "main", fake)
    assert fake.argvs[1] == ["git", "worktree", "add", "-b", "b", "/nowhere/.worktrees/b", "main"]


def test_merge_base_reads_the_common_ancestor() -> None:
    fake = Fake((0, "abc123\n", ""))
    assert git.merge_base(WT, "HEAD", "origin/HEAD", fake) == "abc123"
    assert fake.calls == [(["git", "merge-base", "HEAD", "origin/HEAD"], WT, None)]


def test_clone_root_outside_a_repository_names_the_rule() -> None:
    fake = Fake((128, "", "fatal: not a git repository (or any of the parent directories): .git\n"))
    with pytest.raises(Refusal, match="run deckhand from the clone or a story worktree"):
        git.clone_root(Path("/tmp/scratch"), fake)


def test_every_git_call_disables_hooks_and_fsmonitor(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def recorded(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "abc\n", "")

    monkeypatch.setattr(subprocess, "run", recorded)
    git.head(WT)
    git.run(["sh", "-c", "true"], WT)
    search = git.trusted_path()
    assert seen == [
        [
            shutil.which("git", path=search),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "rev-parse",
            "HEAD",
        ],
        [shutil.which("sh", path=search), "-c", "true"],
    ]


def test_a_call_without_a_directory_runs_in_home(monkeypatch: pytest.MonkeyPatch) -> None:
    where: list[Path | None] = []

    def recorded(argv, **kwargs):
        where.append(kwargs["cwd"])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", recorded)
    git.run(["gh", "pr", "list", "--repo", "acme/widgets"])
    git.run(["git", "status"], WT)
    assert where == [Path.home(), WT]


def plant_git(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / "planted-ran"
    fake = directory / "git"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\necho planted\n")
    fake.chmod(0o755)
    return marker


@pytest.fixture
def real_path() -> str:
    found = shutil.which("git")
    assert found is not None
    return os.environ["PATH"]


def test_planted_git_in_temp_dir_never_runs(tmp_path: Path, real_path: str, monkeypatch: pytest.MonkeyPatch):
    temp = tmp_path / "temp"
    marker = plant_git(temp)
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("PATH", f"{temp}{os.pathsep}{real_path}")
    done = git.run(["git", "--version"])
    assert not marker.exists()
    assert done.stdout.startswith("git version")


@pytest.mark.parametrize("entry", ["", "."])
def test_empty_or_dot_path_entry_ignored(tmp_path: Path, real_path: str, monkeypatch: pytest.MonkeyPatch, entry: str):
    work = tmp_path / "work"
    marker = plant_git(work)
    elsewhere = tmp_path / "elsewhere-temp"
    elsewhere.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(elsewhere))
    monkeypatch.setenv("TMPDIR", str(elsewhere))
    monkeypatch.chdir(work)
    monkeypatch.setenv("PATH", f"{entry}{os.pathsep}{real_path}")
    done = git.run(["git", "--version"], work)
    assert not marker.exists()
    assert done.stdout.startswith("git version")


def test_child_gets_the_cleaned_path(tmp_path: Path, real_path: str, monkeypatch: pytest.MonkeyPatch):
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("PATH", f".{os.pathsep}relative/bin{os.pathsep}{temp}{os.pathsep}{real_path}")
    printed = git.run(["sh", "-c", 'printf %s "$PATH"']).stdout
    assert printed == git.trusted_path()
    assert printed and not {".", "relative/bin", str(temp)} & set(printed.split(os.pathsep))


def test_program_on_no_trusted_entry_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    temp = tmp_path / "temp"
    plant_git(temp)
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("PATH", f".{os.pathsep}{temp}")
    with pytest.raises(git.GitError, match="trusted PATH"):
        git.run(["git", "--version"])


def test_worktree_detached_adds_a_headless_worktree_once(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"],
    ):
        subprocess.run(argv, cwd=clone, check=True)
    first = git.worktree_detached(clone, "spike-abc", "main")
    second = git.worktree_detached(clone, "spike-abc", "main")
    assert first == second == clone / ".worktrees" / "spike-abc"
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=clone, capture_output=True, text=True
    ).stdout
    assert listing.count("worktree ") == 2
    assert "detached" in listing


def test_worktree_detached_never_makes_a_branch() -> None:
    fake = Fake((0, "", ""))
    git.worktree_detached(Path("/nowhere"), "spike-abc", "origin/HEAD", fake)
    assert fake.argvs == [["git", "worktree", "add", "--detach", "/nowhere/.worktrees/spike-abc", "origin/HEAD"]]


def test_two_commits_in_one_worktree_take_turns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clone = repo_with_story(tmp_path, monkeypatch)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "story", str(worktree)], cwd=clone, check=True)
    parked, release, entered = threading.Event(), threading.Event(), threading.Event()
    calls: list[tuple[str, list[str]]] = []
    failures: list[BaseException] = []

    def runner(argv, cwd=None, stdin=None):
        name = threading.current_thread().name
        calls.append((name, argv[:2]))
        if name == "A" and argv[:2] == ["git", "add"]:
            parked.set()
            release.wait(5)
        if name == "B" and argv[:2] == ["git", "ls-tree"]:
            entered.set()
        return git.run(argv, cwd, stdin)

    def commit(message: str) -> None:
        try:
            git.commit_all(worktree, message, runner)
        except BaseException as problem:
            failures.append(problem)

    (worktree / "one.txt").write_text("1\n")
    first = threading.Thread(target=commit, args=("feat: one\n",), name="A")
    first.start()
    assert parked.wait(5)
    (worktree / "two.txt").write_text("2\n")
    second = threading.Thread(target=commit, args=("feat: two\n",), name="B")
    second.start()
    assert not entered.wait(0.5)
    assert [argv for name, argv in calls if name == "B"] == [["git", "rev-parse"]]
    release.set()
    first.join(5)
    second.join(5)
    assert failures == []
    assert calls.index(("A", ["git", "commit"])) < calls.index(("B", ["git", "ls-tree"]))
    log = subprocess.run(["git", "log", "--format=%s"], cwd=worktree, check=True, capture_output=True, text=True)
    assert log.stdout.split("\n")[:3] == ["feat: two", "feat: one", "base"]


def test_fetch_prunes_origin_and_raises_on_failure() -> None:
    fake = Fake((0, "", ""), (128, "", "fatal: unable to access origin"))
    git.fetch(Path("/clone"), fake)
    assert fake.calls[0][:2] == (["git", "fetch", "origin", "--prune"], Path("/clone"))
    with pytest.raises(git.GitError) as raised:
        git.fetch(Path("/clone"), fake)
    assert raised.value.stderr_tail == "fatal: unable to access origin"


SECRETS = ("GH_TOKEN", "LINEAR_API_KEY", "OPENROUTER_API_KEY", "CLOCKIFY_API_KEY", "AWS_SECRET_ACCESS_KEY", "DB_URL")
SESSION = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_TOKEN")
KEPT = (
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "SSH_AUTH_SOCK",
    "HTTPS_PROXY",
    "http_proxy",
    "no_proxy",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_USE_BEDROCK",
    "DECKHAND_STATE_DIR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "GIT_SSH_COMMAND",
    "GIT_SSH",
    "GIT_CONFIG_GLOBAL",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "NODE_EXTRA_CA_CERTS",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "AWS_REGION",
    "AWS_PROFILE",
    "CLOUD_ML_REGION",
)


def test_a_claude_call_carries_only_the_allowed_environment(monkeypatch: pytest.MonkeyPatch):
    for name in list(os.environ):
        monkeypatch.delenv(name)
    for name in (*SECRETS, *SESSION, *KEPT):
        monkeypatch.setenv(name, "value")
    seen = {}

    def run(full, env=None, **kwargs):
        seen[Path(full[0]).name] = env
        return subprocess.CompletedProcess(full, 0, "", "")

    monkeypatch.setattr(git.subprocess, "run", run)
    monkeypatch.setattr(git, "_program", lambda name, search: f"/usr/bin/{name}")
    monkeypatch.setattr(git, "trusted_path", lambda: "/usr/bin")
    git.run(["claude", "agents", "--json", "--all"])
    git.run(["git", "fetch", "origin"])
    assert set(seen["claude"]) == {"PATH", *KEPT}
    assert seen["claude"]["PATH"] == "/usr/bin"
    assert {*SECRETS, *SESSION, *KEPT} <= set(seen["git"])


ALONE = """
import os, sys, threading, time
from deckhand import git
marker = sys.argv[1]
out = {}
done = threading.Event()
def work():
    try:
        out["rc"] = git.alone(["sh", "-c", ': > "$0"; exec sleep 1', marker]).returncode
    finally:
        done.set()
threading.Thread(target=work).start()
deadline = time.monotonic() + 10
while not os.path.exists(marker) and not done.is_set() and time.monotonic() < deadline:
    time.sleep(0.01)
print("started" if os.path.exists(marker) else "not started", flush=True)
# On Python 3.12 a Thread.join interrupted by Ctrl-C marks the thread finished while it still runs.
while True:
    try:
        done.wait()
        break
    except KeyboardInterrupt:
        pass
print(out.get("rc"), flush=True)
"""


def test_a_child_run_alone_survives_a_ctrl_c_sent_to_its_parents_group(tmp_path: Path) -> None:
    import signal
    import sys

    root = Path(__file__).resolve().parent.parent
    parent = subprocess.Popen(
        [sys.executable, "-c", ALONE, str(tmp_path / "started")],
        cwd=root,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert parent.stdout is not None and parent.stdout.readline() == "started\n"
        os.killpg(parent.pid, signal.SIGINT)
        out, _ = parent.communicate(timeout=10)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.communicate()
    assert out.strip() == "0"


def test_alone_gives_a_child_no_terminal_input():
    assert git.alone(["cat"]).stdout == ""


@pytest.mark.parametrize(
    "stderr",
    [
        "fatal: could not read Username for 'https://github.com': Device not configured\n",
        "ssh_askpass: exec(/usr/bin/ssh-askpass): No such file or directory\n"
        "Host key verification failed: read_passphrase: can't open /dev/tty: Device not configured\n",
    ],
)
def test_a_prompt_with_no_terminal_says_how_to_load_the_key(stderr: str) -> None:
    fake = Fake((128, "", stderr))
    with pytest.raises(git.GitError) as caught:
        git.push(WT, "b", fake)
    assert caught.value.stderr_tail == git.NO_PROMPT
    assert "Device not configured" not in str(caught.value)
