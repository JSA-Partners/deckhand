from __future__ import annotations

import itertools
import os
import subprocess
from pathlib import Path

from deckhand import cli, document  # noqa: F401  (registers "document" before the registry snapshot)
from tests.conftest import run_deckhand

# Git's %ci has one-second resolution; a fast test can make two commits in the same second,
# which would make a "committed later" comparison flaky. Force each commit to a distinct,
# strictly increasing timestamp instead of relying on wall-clock spacing.
_next_commit_time = itertools.count(1_700_000_000, 60)


def _commit(repo: Path, message: str) -> None:
    when = f"{next(_next_commit_time)} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True, env=env)


def test_audit_prints_no_docs_directory_message_when_absent(repo):
    result = run_deckhand("document", "context", cwd=repo)

    assert result.returncode == 0
    assert result.stdout.strip() == "No docs directory at docs/claude."


def test_audit_reports_nothing_stale_for_a_fully_clean_tree(repo):
    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    (docs / "clean.md").write_text("## Only Here\n\nNo references, no duplicates.\n")
    _commit(repo, "add clean doc")

    result = run_deckhand("document", "context", cwd=repo)

    assert result.returncode == 0
    assert result.stdout.strip() == "Nothing stale."


def test_audit_finds_a_stale_reference_a_duplicate_heading_and_leaves_the_clean_file_alone(repo):
    # A referenced file, committed first.
    lib = repo / "lib"
    lib.mkdir()
    (lib / "thing.py").write_text("x = 1\n")
    _commit(repo, "add thing")

    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    (docs / "a.md").write_text("## Setup\n\nSee `lib/thing.py` for details.\n")
    (docs / "b.md").write_text("## Setup\n\nA different file with the same heading.\n")
    (docs / "clean.md").write_text("## Only Here\n\nNothing to see.\n")
    _commit(repo, "add docs")

    # Change the referenced file after the doc that cites it was last committed.
    (lib / "thing.py").write_text("x = 2\n")
    _commit(repo, "change thing")

    result = run_deckhand("document", "context", cwd=repo)

    assert result.returncode == 0
    out = result.stdout

    assert "docs/claude/a.md:" in out
    assert "stale reference: `lib/thing.py`" in out
    assert 'duplicate heading "Setup" also in docs/claude/b.md' in out

    assert "docs/claude/b.md:" in out
    assert 'duplicate heading "Setup" also in docs/claude/a.md' in out

    assert "docs/claude/clean.md" not in out


def test_audit_finds_a_broken_reference(repo):
    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    (docs / "broken.md").write_text("See `lib/missing.py` for details.\n")
    _commit(repo, "add broken doc")

    result = run_deckhand("document", "context", cwd=repo)

    assert result.returncode == 0
    assert "docs/claude/broken.md:" in result.stdout
    assert "broken reference: `lib/missing.py`" in result.stdout


def test_audit_finds_a_broken_reference_whatever_the_extension(repo):
    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    (docs / "rust.md").write_text("Ported into `internal/store/collection.rs`.\n")
    _commit(repo, "add rust doc")

    result = run_deckhand("document", "context", cwd=repo)

    assert result.returncode == 0
    assert "broken reference: `internal/store/collection.rs`" in result.stdout


def test_audit_checks_a_bare_name_that_exists_and_ignores_one_that_does_not(repo):
    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    (docs / "helper.rs").write_text("fn main() {}\n")
    _commit(repo, "add helper")

    (docs / "a.md").write_text("See `helper.rs`, and `gone.rs` which is nowhere.\n")
    _commit(repo, "add doc")

    (docs / "helper.rs").write_text("fn main() { todo!() }\n")
    _commit(repo, "change helper")

    result = run_deckhand("document", "context", cwd=repo)

    assert result.returncode == 0
    assert "stale reference: `helper.rs`" in result.stdout
    assert "gone.rs" not in result.stdout


def test_audit_resolves_sibling_references_inside_the_docs_tree(repo):
    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    (docs / "reference.md").write_text("## Reference\n\nDetails.\n")
    (docs / "index.md").write_text("## Index\n\nSee `reference.md` and `./reference.md`.\n")
    _commit(repo, "add docs")

    result = run_deckhand("document", "context", cwd=repo)

    assert result.returncode == 0
    assert "broken reference" not in result.stdout
    assert result.stdout.strip() == "Nothing stale."


def test_audit_runs_one_git_log_per_distinct_path(repo, monkeypatch):
    lib = repo / "lib"
    lib.mkdir()
    (lib / "thing.py").write_text("x = 1\n")
    (lib / "other.py").write_text("y = 1\n")
    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    (docs / "a.md").write_text("See `lib/thing.py`, `lib/thing.py:1`, and `lib/other.py`.\n")
    (docs / "b.md").write_text("See `lib/thing.py` and `lib/other.py` again.\n")
    _commit(repo, "add docs and lib")

    real_run = subprocess.run
    git_log_calls: list[list[str]] = []

    def counting_run(cmd, *args, **kwargs):
        if cmd[:2] == ["git", "log"]:
            git_log_calls.append(list(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting_run)

    exit_code = cli.main(["document", "context"])

    assert exit_code == 0
    logged = sorted(Path(call[-1]).relative_to(repo).as_posix() for call in git_log_calls)
    assert logged == ["docs/claude/a.md", "docs/claude/b.md", "lib/other.py", "lib/thing.py"]


def test_audit_reports_a_heading_repeated_within_one_file_once(repo):
    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    (docs / "x.md").write_text("## Setup\n\nFirst.\n\n## Setup\n\nSecond.\n")
    _commit(repo, "add doc")

    result = run_deckhand("document", "context", cwd=repo)

    assert result.returncode == 0
    assert result.stdout.count('duplicate heading "Setup" repeated in docs/claude/x.md') == 1
    assert "also in" not in result.stdout


def test_audit_ignores_versions_domains_and_fenced_code(repo):
    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    (docs / "y.md").write_text(
        "Pinned at `1.2.3`, hosted on `github.com`.\n"
        "\n"
        "```\n"
        "See `lib/missing.py` inside a fence.\n"
        "```\n"
        "\n"
        "~~~sh\n"
        "cat `lib/also-missing.sh`\n"
        "~~~\n"
    )
    _commit(repo, "add doc")

    result = run_deckhand("document", "context", cwd=repo)

    assert result.returncode == 0
    assert result.stdout.strip() == "Nothing stale."


def test_audit_ignores_indented_and_unterminated_fences(repo):
    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    (docs / "indented.md").write_text(
        "- A list item with a fenced block:\n\n  ```sh\n  cat `lib/missing.py`\n  ```\n\nThen prose.\n"
    )
    (docs / "unterminated.md").write_text(
        "Prose first.\n\n```\nSee `lib/also-missing.py` in a fence that never closes.\n"
    )
    _commit(repo, "add docs")

    result = run_deckhand("document", "context", cwd=repo)

    assert result.returncode == 0
    assert result.stdout.strip() == "Nothing stale."


def test_audit_accepts_a_custom_directory(repo):
    other = repo / "notes"
    other.mkdir()
    (other / "x.md").write_text("## X\n\nSome text.\n")
    _commit(repo, "add notes")

    result = run_deckhand("document", "context", "notes", cwd=repo)

    assert result.returncode == 0
    assert result.stdout.strip() == "Nothing stale."


def test_audit_reports_a_failure_as_the_steps_one_line(repo, monkeypatch, capsys):
    def boom(dir_arg: str) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(document, "_audit", boom)

    exit_code = cli.main(["document", "context"])

    assert exit_code == 0
    assert capsys.readouterr().out == "(deckhand document context failed: boom. Say what could not be read and stop.)\n"


def test_audit_never_exits_non_zero_when_git_is_unavailable(repo, monkeypatch):
    # In-process (not a subprocess): a bare/nonexistent PATH would also break the
    # `#!/usr/bin/env python3` shebang that run_deckhand relies on to launch at all.
    docs = repo / "docs" / "claude"
    docs.mkdir(parents=True)
    lib = repo / "lib"
    lib.mkdir()
    (lib / "thing.py").write_text("x = 1\n")
    (docs / "a.md").write_text("See `lib/thing.py` for details.\n")
    _commit(repo, "add docs and thing")

    monkeypatch.setenv("PATH", "/nonexistent")

    exit_code = cli.main(["document", "context"])

    assert exit_code == 0
