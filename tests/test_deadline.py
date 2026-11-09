"""Tests for deckhand.deadline, through the gh calls it limits."""

import os
import subprocess
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


def _sleeper() -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["sleep", "30"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )


def test_an_interrupt_kills_the_call_it_was_waiting_on(monkeypatch):
    """gh runs in its own session, so a Ctrl-C reaching deckhand never reaches it; the wait kills it."""
    process = _sleeper()
    calls = iter([0.0])

    def interrupted():
        try:
            return next(calls)
        except StopIteration:
            raise KeyboardInterrupt from None

    monkeypatch.setattr(deadline, "_now", interrupted)
    with pytest.raises(KeyboardInterrupt):
        deadline.wait(process, None)
    assert process.poll() is not None


def test_a_call_that_ends_as_it_is_killed_still_reads_as_expired(monkeypatch):
    process = _sleeper()
    monkeypatch.setattr(deadline, "LIMIT", 0.5)

    def gone(pid, sig):
        os.kill(pid, sig)
        raise ProcessLookupError

    monkeypatch.setattr(deadline.os, "killpg", gone)
    with pytest.raises(deadline.Expired):
        deadline.wait(process, None)


@pytest.mark.parametrize(
    "args",
    [
        ("project", "item-archive", "2"),
        ("project", "item-delete", "2"),
        ("project", "field-create", "2"),
        ("project", "field-delete", "--id", "F"),
        ("project", "link", "2"),
        ("repo", "edit", "acme/widgets"),
    ],
)
def test_every_cli_write_may_have_landed(args):
    assert deadline.writes(args)


def test_a_mutation_sent_on_stdin_may_have_landed():
    args = ("api", "graphql", "--input", "-")
    assert deadline.writes(args, '{"query": "mutation($id: ID!) { deleteItem(id: $id) { id } }"}')
    assert not deadline.writes(args, '{"query": "query { viewer { login } }"}')
