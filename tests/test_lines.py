from __future__ import annotations

from pathlib import Path

from deckhand import fleet
from deckhand.actions import Attach, Launch, Resume
from deckhand.lines import Lines, Stories
from deckhand.reconcile import RETRY_AFTER

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


def attach() -> Attach:
    return Attach("mirror:s1", "mirror:s1:https://x", "d1", "s1", "https://x", "Plan approved by Ada", "", ())


def test_a_failed_mirror_backs_off_across_a_settle_until_it_lands():
    lines = Lines()
    lines.heard(attach(), "mirror:s1: network: Linear is unreachable", 100.0)
    lines.settle(fleet.Fleet([]), Stories(frozenset(), frozenset(), frozenset()))
    assert lines.backing_off(attach(), 110.0)
    assert not lines.backing_off(attach(), 100.0 + RETRY_AFTER)
    assert lines.newest() == "mirror:s1: network: Linear is unreachable"
    lines.heard(attach(), None, 200.0)
    assert not lines.backing_off(attach(), 201.0)
    assert lines.newest() == ""


def test_a_failed_mirror_stays_backed_off_when_its_sibling_on_the_story_lands():
    lines = Lines()
    sign = Attach("mirror:s1", "mirror:s1:https://github.com/c2", "d2", "s1", "https://github.com/c2", "S", "", ())
    lines.heard(attach(), "mirror:s1: network: Linear is unreachable", 100.0)
    lines.heard(sign, None, 101.0)
    assert lines.backing_off(attach(), 110.0)
    assert not lines.backing_off(sign, 110.0)
