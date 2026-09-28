from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deckhand import reviewed
from tests.conftest import run_deckhand

NUMBER = 248
ISSUE_URL = "https://github.com/acme/widgets/issues/248"
TITLE = "Guest users see only their granted collections"


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


def _sha(path: Path, ref: str) -> str:
    return _git(path, "rev-parse", ref).strip()


def _posted(copy: Path) -> str:
    text = copy.read_text(encoding="utf-8")
    marker = "--- issue comment\n"
    assert text.startswith(marker)
    return text[len(marker) :]


def _apply(repo: Path, *extra: str, env: dict[str, str] | None = None):
    return run_deckhand("reviewed", "apply", str(NUMBER), *extra, cwd=repo, env=env or {})


# --- context -----------------------------------------------------------


def test_context_prints_the_story_the_branch_and_the_commit(fake_gh, repo):
    result = run_deckhand("reviewed", "context", str(NUMBER), cwd=repo)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"Title: {TITLE}"
    assert lines[1] == f"Issue: {ISSUE_URL}"
    assert lines[2] == "Branch: main"
    assert lines[3] == f"Commit: {_sha(repo, 'HEAD')}"
    assert lines[-1] == f'Apply: deckhand reviewed apply {NUMBER} "<one line>"'


def test_context_never_fails_when_the_issue_cannot_be_read(fake_gh, repo):
    result = run_deckhand("reviewed", "context", str(NUMBER), cwd=repo, env={"GH_ISSUE_VIEW_FAILS": str(NUMBER)})

    assert result.returncode == 0
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert lines[0].startswith("Title: unavailable (")
    assert lines[1] == "Branch: main"
    assert lines[2] == f"Commit: {_sha(repo, 'HEAD')}"


def test_context_never_fails_outside_a_git_repository(fake_gh, tmp_path):
    result = run_deckhand("reviewed", "context", str(NUMBER), cwd=tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert lines[0] == f"Title: {TITLE}"
    assert lines[1] == f"Issue: {ISSUE_URL}"
    assert lines[2].startswith("Branch: unavailable (")
    assert lines[3].startswith("Commit: unavailable (")


# --- apply ---------------------------------------------------------------


def test_apply_writes_the_ref_at_head_and_posts_one_comment(fake_gh, repo, tmp_path):
    copy = tmp_path / "comment.md"
    head = _sha(repo, "HEAD")

    result = _apply(repo, "Clean pass.", env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert _sha(repo, reviewed.ref_name(NUMBER)) == head
    assert _posted(copy).splitlines()[0] == f"Reviewed: {head} Clean pass."


def test_apply_records_head_not_a_sha_the_caller_passed(fake_gh, repo, tmp_path):
    """The sha comes from HEAD; a plausible-looking sha typed into the summary must not reach the ref."""
    copy = tmp_path / "comment.md"
    head = _sha(repo, "HEAD")
    decoy = "0" * 40
    assert decoy != head

    result = _apply(repo, f"Clean pass, see {decoy}.", env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert _sha(repo, reviewed.ref_name(NUMBER)) == head
    assert _posted(copy).splitlines()[0] == f"Reviewed: {head} Clean pass, see {decoy}."


def test_apply_refuses_an_empty_summary_and_writes_nothing(fake_gh, gh_calls, repo):
    result = _apply(repo, "   ")

    assert result.returncode == 1
    assert result.stderr == "deckhand reviewed apply: the summary needs a line on the pass\n"
    assert [c for c in gh_calls() if c.startswith("issue comment")] == []
    with pytest.raises(subprocess.CalledProcessError):
        _git(repo, "rev-parse", "--verify", reviewed.ref_name(NUMBER))
