from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deckhand import git


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


def test_apply_hunks_commits_on_base_and_returns_sha() -> None:
    fake = Fake((0, "", ""), (0, "", ""), (0, "", ""), (0, "sha9\n", ""))
    assert git.apply_hunks(WT, "main", "PATCH", "scratch", fake) == "sha9"
    assert fake.argvs == [
        ["git", "checkout", "--detach", "main"],
        ["git", "apply", "--index", "-"],
        ["git", "commit", "-m", "scratch"],
        ["git", "rev-parse", "HEAD"],
    ]
    assert fake.calls[1][2] == "PATCH"


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
