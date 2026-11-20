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
