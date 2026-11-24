from __future__ import annotations

from pathlib import Path

from deckhand.lines import Lines
from deckhand.reconcile import Launch, Resume

NAME = "hand-ACME-1"


def launch() -> Launch:
    return Launch(NAME, "ACME-1", "story", Path("/clone"), None, "viewer-1")


def resume() -> Resume:
    return Resume(NAME, "abc12345", "/clone", "Go on.", ())


def test_alternating_launch_and_resume_never_lowers_the_backoff():
    lines = Lines()
    lines.heard(launch(), "cannot fetch origin", 100.0)
    lines.heard(launch(), "cannot fetch origin", 200.0)
    lines.heard(launch(), "cannot fetch origin", 300.0)
    lines.heard(resume(), "cannot resume", 400.0)
    assert lines.failed() == {NAME: (3, 400.0)}


def test_a_name_that_failed_once_counts_one_try():
    lines = Lines()
    lines.heard(resume(), "cannot resume", 50.0)
    assert lines.failed() == {NAME: (1, 50.0)}
