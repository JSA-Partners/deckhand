"""A parked feature: where it opens, what it says it came from, and what it waits on."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import run_deckhand

FEATURE = "## Requirements\n\nGuests should be able to share a collection with a guest.\n"
TITLE = "Share a collection"


def _park(repo: Path, tmp_path: Path, *flags: str, copy: Path | None = None):
    """Park FEATURE with `flags`; `copy` collects the bodies, which reach gh through a file."""
    source = tmp_path / "feature.md"
    source.write_text(FEATURE, encoding="utf-8")
    env = {"GH_BODY_FILE_COPY": str(copy)} if copy is not None else {}
    return run_deckhand("new", "apply", "--park", *flags, str(source), cwd=repo, env=env)


def test_from_names_the_origin_and_writes_no_dependency(fake_gh, gh_calls, repo, tmp_path):
    copy = tmp_path / "bodies.md"

    result = _park(repo, tmp_path, "--title", TITLE, "--from", "251", copy=copy)

    assert result.returncode == 0, result.stderr
    assert "Drafted: parked from #251" in copy.read_text(encoding="utf-8")
    assert [c for c in gh_calls() if "dependencies/blocked_by" in c] == []


def test_a_park_with_no_origin_names_its_repository(fake_gh, repo, tmp_path):
    copy = tmp_path / "bodies.md"

    result = _park(repo, tmp_path, "--title", TITLE, copy=copy)

    assert result.returncode == 0, result.stderr
    assert "Drafted: parked from acme/widgets" in copy.read_text(encoding="utf-8")


def test_after_records_what_the_park_waits_on(fake_gh, repo, tmp_path):
    result = _park(repo, tmp_path, "--title", TITLE, "--after", "251")

    assert result.returncode == 0, result.stderr
    assert "blocked by #251" in result.stdout


def test_blocks_still_names_the_origin_and_writes_the_dependency(fake_gh, gh_calls, repo, tmp_path):
    copy = tmp_path / "bodies.md"

    result = _park(repo, tmp_path, "--title", TITLE, "--blocks", "251", copy=copy)

    assert result.returncode == 0, result.stderr
    assert "Drafted: parked from #251" in copy.read_text(encoding="utf-8")
    assert [c for c in gh_calls() if "dependencies/blocked_by" in c] != []


def test_from_and_blocks_together_are_a_usage_error(fake_gh, repo, tmp_path):
    result = _park(repo, tmp_path, "--title", TITLE, "--from", "251", "--blocks", "252")

    assert result.returncode == 2
    assert "--from and --blocks" in result.stderr


def test_after_outside_a_park_is_a_usage_error(fake_gh, repo, tmp_path):
    source = tmp_path / "feature.md"
    source.write_text(FEATURE, encoding="utf-8")

    result = run_deckhand("new", "apply", "--split", "--after", "251", str(source), cwd=repo, env={})

    assert result.returncode == 2
    assert "--after" in result.stderr


def test_a_bad_after_reference_is_refused_before_any_write(fake_gh, gh_calls, repo, tmp_path):
    result = _park(repo, tmp_path, "--title", TITLE, "--after", "not-a-number")

    assert result.returncode == 1
    assert "owner/name#M" in result.stderr
    assert [c for c in gh_calls() if c.startswith("issue create")] == []
