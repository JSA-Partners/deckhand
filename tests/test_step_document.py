from __future__ import annotations

import itertools
import os
import subprocess
from pathlib import Path

import pytest

from deckhand.step import Refusal
from deckhand.steps.document import Document
from tests.test_step_commit import nested_in_a_hand_worktree

TIMES = itertools.count(1_700_000_000, 60)


def git(repo: Path, *args: str) -> str:
    when = f"{next(TIMES)} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    done = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env)
    return done.stdout.strip()


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@t")
    git(path, "config", "user.name", "t")
    (path / "f").write_text("x\n")
    commit(path, "init")
    return path


def docs(repo: Path, **files: str) -> Path:
    where = repo / "docs" / "claude"
    where.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (where / f"{name}.md").write_text(text)
    return where


def shown(repo: Path, capsys: pytest.CaptureFixture[str], *args: str) -> str:
    capsys.readouterr()
    Document(cwd=repo).context(list(args))
    return capsys.readouterr().out


def test_no_docs_directory_is_said(repo: Path, capsys: pytest.CaptureFixture[str]):
    assert shown(repo, capsys) == "No docs directory at docs/claude.\n"


def test_clean_docs_are_nothing_stale(repo: Path, capsys: pytest.CaptureFixture[str]):
    docs(repo, clean="## Only Here\n\nSee `f` and `1.2.3`.\n\n```\n`gone/x.py`\n```\n")
    commit(repo, "docs")
    assert shown(repo, capsys) == "Nothing stale.\n"


def test_stale_broken_and_duplicate_are_reported(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / "lib").mkdir()
    (repo / "lib" / "thing.py").write_text("x = 1\n")
    commit(repo, "thing")
    docs(repo, a="## Setup\n\nSee `lib/thing.py`.\n", b="## Setup\n\nSee `lib/gone.py`.\n")
    commit(repo, "docs")
    (repo / "lib" / "thing.py").write_text("x = 2\n")
    commit(repo, "change thing")
    out = shown(repo, capsys)
    assert "stale reference: `lib/thing.py`" in out.split("docs/claude/b.md")[0]
    assert "broken reference: `lib/gone.py`" in out
    assert 'duplicate heading "Setup" also in docs/claude/b.md' in out


def test_a_custom_directory_is_audited(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / "notes").mkdir()
    (repo / "notes" / "n.md").write_text("See `lib/gone.py`.\n")
    commit(repo, "notes")
    assert "broken reference" in shown(repo, capsys, "notes")


def test_docs_naming_a_file_this_branch_changed_are_listed(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / "app.py").write_text("x = 1\n")
    docs(repo, a="See `app.py`.\n")
    commit(repo, "docs")
    git(repo, "update-ref", "refs/remotes/origin/HEAD", git(repo, "rev-parse", "HEAD"))
    (repo / "app.py").write_text("x = 2\n")
    commit(repo, "change app")
    out = shown(repo, capsys)
    assert "Docs naming a file this branch changed:\n  docs/claude/a.md: `app.py`" in out


def test_a_hand_audits_only_from_a_listed_worktree(repo: Path, monkeypatch, capsys: pytest.CaptureFixture[str]):
    worktree, nested = nested_in_a_hand_worktree(repo, monkeypatch)
    monkeypatch.chdir(nested)
    with pytest.raises(Refusal, match="a hand runs deckhand only from its story worktree"):
        Document().context([])
    monkeypatch.chdir(worktree)
    Document().context([])
    assert capsys.readouterr().out == "No docs directory at docs/claude.\n"
