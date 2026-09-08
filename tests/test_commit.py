from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deckhand import cli, commit
from tests.conftest import run_deckhand


@pytest.fixture
def history_repo(repo: Path) -> Path:
    """`repo` plus a second commit, so recent history has two conventional subjects."""
    (repo / "internal" / "auth").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "lib" / "api").mkdir(parents=True, exist_ok=True)
    (repo / "internal" / "auth" / "a.go").write_text("a\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feat(auth): first"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "fix(api): second"], cwd=repo, check=True)
    return repo


def test_reports_nothing_staged(history_repo):
    (history_repo / "internal" / "auth" / "b.go").write_text("b\n")

    result = run_deckhand("commit", "context", cwd=history_repo)

    assert result.returncode == 0
    assert "Nothing is staged." in result.stdout
    assert "internal/auth/b.go" in result.stdout


def test_reports_staged_diff_history_convention_and_scope(history_repo):
    (history_repo / "internal" / "auth" / "b.go").write_text("b\n")
    (history_repo / "package.json").write_text('{"commitlint":{"extends":["@commitlint/config-conventional"]}}')
    subprocess.run(["git", "add", "internal/auth/b.go"], cwd=history_repo, check=True)

    result = run_deckhand("commit", "context", cwd=history_repo)

    out = result.stdout
    assert "## Staged" in out and "internal/auth/b.go" in out
    assert "## Diff" in out and "+b" in out
    assert "## Recent history" in out and "fix(api): second" in out and "feat(auth): first" in out
    assert "## Convention" in out
    assert out.index("conventional commits (required)") < out.index("Recent history:")
    assert "Recent history:" in out and out.index("Recent history:") < out.index("commitlint: package.json")
    assert "commitlint: package.json" in out
    assert "- Inferred scope: auth" in out


def test_scope_is_empty_when_staged_files_disagree(history_repo):
    (history_repo / "internal" / "auth" / "b.go").write_text("b\n")
    (history_repo / "src" / "lib" / "api" / "c.ts").write_text("c\n")
    subprocess.run(["git", "add", "internal/auth/b.go", "src/lib/api/c.ts"], cwd=history_repo, check=True)

    result = run_deckhand("commit", "context", cwd=history_repo)

    assert "- Inferred scope: (none, files span api and auth)" in result.stdout


def test_reads_a_claude_md_commit_convention(history_repo):
    (history_repo / "CLAUDE.md").write_text("## Commits\n\nUse the ticket number as scope.\n")
    (history_repo / "internal" / "auth" / "b.go").write_text("b\n")
    subprocess.run(["git", "add", "internal/auth/b.go"], cwd=history_repo, check=True)

    result = run_deckhand("commit", "context", cwd=history_repo)

    assert "CLAUDE.md mentions commits" in result.stdout
    assert "ticket number" in result.stdout


def test_handles_a_repo_with_no_commits(tmp_path):
    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=empty_repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=empty_repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=empty_repo, check=True)
    (empty_repo / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "a.txt"], cwd=empty_repo, check=True)

    result = run_deckhand("commit", "context", cwd=empty_repo)

    assert result.returncode == 0
    assert "## Recent history" in result.stdout
    assert "- (no commits yet)" in result.stdout
    assert "## Convention" in result.stdout
    assert "- Recent history: no commits yet" in result.stdout


def test_widens_the_fence_when_the_diff_contains_triple_backticks(history_repo):
    (history_repo / "doc.md").write_text("# doc\n\n```\ncode\n```\n")
    subprocess.run(["git", "add", "doc.md"], cwd=history_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "docs: add doc"], cwd=history_repo, check=True)
    (history_repo / "doc.md").write_text("# doc\n\n```\ncode2\n```\n")
    subprocess.run(["git", "add", "doc.md"], cwd=history_repo, check=True)

    result = run_deckhand("commit", "context", cwd=history_repo)

    assert result.returncode == 0
    assert "\n````diff\n" in result.stdout
    assert "\n````\n\n## Recent history\n" in result.stdout


def test_reports_root_level_files_without_a_scope(history_repo):
    (history_repo / "README.md").write_text("root change\n")
    subprocess.run(["git", "add", "README.md"], cwd=history_repo, check=True)

    result = run_deckhand("commit", "context", cwd=history_repo)

    assert "- Inferred scope: (none, root-level files)" in result.stdout


def test_diff_is_truncated_at_600_lines(repo):
    lines = "\n".join(f"line {i}" for i in range(700))
    (repo / "big.txt").write_text(lines + "\n")
    subprocess.run(["git", "add", "big.txt"], cwd=repo, check=True)

    result = run_deckhand("commit", "context", cwd=repo)

    assert "truncated at 600 lines" in result.stdout


def test_not_a_git_repository_never_fails(tmp_path):
    result = run_deckhand("commit", "context", cwd=tmp_path)

    assert result.returncode == 0


def test_a_latin1_diff_still_produces_the_full_report(repo):
    (repo / "l.txt").write_bytes(b"caf\xe9\n")
    subprocess.run(["git", "add", "l.txt"], cwd=repo, check=True)

    result = run_deckhand("commit", "context", cwd=repo)

    assert result.returncode == 0
    assert "codec" not in result.stdout
    assert "## Diff" in result.stdout and "+caf�" in result.stdout
    assert "## Convention" in result.stdout and "- Inferred scope:" in result.stdout


def test_crlf_diff_lines_are_printed_intact(repo):
    (repo / "w.txt").write_bytes(b"a\r\nb\r\n")
    subprocess.run(["git", "add", "w.txt"], cwd=repo, check=True)

    report = commit.commit_context(repo)

    assert "+a\r\n+b\r\n" in report


def test_outside_a_repository_prints_one_short_line(tmp_path):
    result = run_deckhand("commit", "context", cwd=tmp_path)

    assert result.returncode == 0
    assert len(result.stdout.splitlines()) <= 2
    assert result.stdout.startswith("(deckhand commit context failed: ")


def test_widens_the_fence_past_a_four_backtick_run(history_repo):
    (history_repo / "doc.md").write_text("# doc\n\n````\ncode\n````\n")
    subprocess.run(["git", "add", "doc.md"], cwd=history_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "docs: add doc"], cwd=history_repo, check=True)
    (history_repo / "doc.md").write_text("# doc\n\n````\ncode2\n````\n")
    subprocess.run(["git", "add", "doc.md"], cwd=history_repo, check=True)

    result = run_deckhand("commit", "context", cwd=history_repo)

    assert "\n`````diff\n" in result.stdout
    assert "\n`````\n\n## Recent history\n" in result.stdout


def test_a_diff_of_exactly_the_limit_is_not_truncated(repo):
    # A new file's diff is six header lines plus one per added line.
    added = commit.DIFF_LIMIT - 6
    (repo / "big.txt").write_text("\n".join(f"line {i}" for i in range(added)) + "\n")
    subprocess.run(["git", "add", "big.txt"], cwd=repo, check=True)

    result = run_deckhand("commit", "context", cwd=repo)

    assert f"+line {added - 1}\n" in result.stdout
    assert "truncated" not in result.stdout
    lines = result.stdout.split("\n")
    opening = lines.index("```diff")
    closing = lines.index("```", opening)
    assert closing - opening - 1 == commit.DIFF_LIMIT


def test_commit_context_never_fails_under_an_os_error(repo, monkeypatch, capsys):
    def boom(*args, cwd=None):
        raise OSError("disk on fire")

    monkeypatch.setattr(commit.git, "run", boom)

    assert cli.main(["commit", "context"]) == 0
    assert capsys.readouterr().out == "(deckhand commit context failed: disk on fire. Continue without it.)\n"
