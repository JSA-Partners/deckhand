from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deckhand.step import Refusal
from deckhand.steps.commit import Commit


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@t")
    git(path, "config", "user.name", "t")
    (path / "internal" / "auth").mkdir(parents=True)
    (path / "internal" / "auth" / "a.go").write_text("a\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "feat(auth): first")
    git(path, "commit", "-q", "--allow-empty", "-m", "fix(api): second")
    return path


def shown(repo: Path, capsys: pytest.CaptureFixture[str]) -> str:
    capsys.readouterr()
    Commit(cwd=repo).context([])
    return capsys.readouterr().out


def test_nothing_staged_names_the_files_and_how_to_stage_them(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / "internal" / "auth" / "b c.go").write_text("b\n")
    out = shown(repo, capsys)
    assert out.startswith("Nothing is staged.")
    assert "Stage: git add -- 'internal/auth/b c.go'" in out


def test_staged_change_reports_diff_history_convention_and_scope(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / "internal" / "auth" / "b.go").write_text("b\n")
    (repo / "package.json").write_text('{"commitlint":{"extends":["@commitlint/config-conventional"]}}')
    (repo / "CLAUDE.md").write_text("## Commits\n\nUse the ticket number as scope.\n")
    git(repo, "add", "internal/auth/b.go")
    out = shown(repo, capsys)
    assert "+b" in out and "fix(api): second" in out
    assert "follows `type(scope): summary` (2/2 commits match)" in out
    assert "commitlint: package.json" in out and "ticket number" in out
    assert "- Inferred scope: auth" in out
    assert "Stage:" not in out


def test_scope_is_none_when_staged_files_disagree(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / "src" / "lib" / "api").mkdir(parents=True)
    (repo / "src" / "lib" / "api" / "c.ts").write_text("c\n")
    (repo / "internal" / "auth" / "b.go").write_text("b\n")
    git(repo, "add", ".")
    assert "- Inferred scope: (none, files span api and auth)" in shown(repo, capsys)


def test_fence_outgrows_backticks_in_the_diff(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / "notes.md").write_text("````\ncode\n````\n")
    git(repo, "add", "notes.md")
    out = shown(repo, capsys)
    assert "`````diff" in out and "- Inferred scope: (none, root-level files)" in out


def test_a_long_diff_is_truncated(repo: Path, capsys: pytest.CaptureFixture[str]):
    (repo / "long.txt").write_text("".join(f"{n}\n" for n in range(700)))
    git(repo, "add", "long.txt")
    assert "truncated at 600 lines" in shown(repo, capsys)


def test_apply_refuses(repo: Path):
    with pytest.raises(Refusal, match="no apply"):
        Commit(cwd=repo).apply([])
