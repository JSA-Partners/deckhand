"""Tests for deckhand.deadline, through the gh calls it limits."""

import os
import time
from pathlib import Path

import pytest

from deckhand import deadline, gh


def _slow_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A gh that never answers, first on PATH."""
    bin_dir = tmp_path / "slow"
    bin_dir.mkdir()
    script = bin_dir / "gh"
    script.write_text("#!/bin/sh\nsleep 30\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_run_gives_up_at_the_deadline(tmp_path, monkeypatch):
    _slow_gh(tmp_path, monkeypatch)
    monkeypatch.setattr(deadline, "LIMIT", 0.5)
    started = time.time()
    with pytest.raises(gh.GhError) as raised:
        gh.run("api", "graphql")
    assert str(raised.value) == "gh api graphql gave no answer in 0.5 seconds"
    assert time.time() - started < 5


def test_deadline_counts_wall_clock_time(tmp_path, monkeypatch):
    _slow_gh(tmp_path, monkeypatch)
    ticks = iter([0.0])
    monkeypatch.setattr(deadline, "_now", lambda: next(ticks, 1000.0))
    started = time.time()
    with pytest.raises(gh.GhError):
        gh.run("api", "user")
    assert time.time() - started < 5


def test_timed_out_write_says_it_may_have_landed(tmp_path, monkeypatch):
    _slow_gh(tmp_path, monkeypatch)
    monkeypatch.setattr(deadline, "LIMIT", 0.5)
    with pytest.raises(gh.GhError) as raised:
        gh.run("issue", "create", "--repo", "acme/widgets")
    assert str(raised.value).endswith("it may have landed; check before running it again")
    with pytest.raises(gh.GhError) as read:
        gh.run("api", "user")
    assert "may have landed" not in str(read.value)
