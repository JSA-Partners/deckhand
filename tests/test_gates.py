"""The gates a branch must pass before `finish` opens its pull request."""

from __future__ import annotations

import pytest

from deckhand import gates
from deckhand.step import TAIL, Refusal


def test_check_returns_when_the_command_exits_zero():
    assert gates.check("true") is None


def test_check_refuses_a_failing_command_and_names_it():
    with pytest.raises(Refusal) as refused:
        gates.check("false")
    assert str(refused.value).splitlines()[0] == "check failed: false"


def test_check_truncates_a_long_failure_to_the_last_tail_lines():
    lines = [f"line{n}" for n in range(1, TAIL + 6)]
    command = "; ".join([*(f"echo {line}" for line in lines), "exit 1"])

    with pytest.raises(Refusal) as refused:
        gates.check(command)

    assert str(refused.value).splitlines()[1:] == [f"  {line}" for line in lines[-TAIL:]]


# --- _fault -------------------------------------------------------------


def test_fault_accepts_a_conventional_subject():
    assert gates._fault("fix(store): tidy the grant filter") is None


def test_fault_rejects_an_unconventional_subject():
    assert gates._fault("made the thing work") == "not a conventional commit subject: made the thing work"


def test_fault_rejects_a_story_number_in_the_subject():
    assert gates._fault("fix(store): close #248") == "a story number in the subject: fix(store): close #248"


def test_fault_rejects_a_wip_subject():
    subject = "fix(store): wip on the grant filter"
    assert gates._fault(subject) == f"work in progress: {subject}"


# --- _attribution ---------------------------------------------------------


def test_attribution_is_none_for_a_clean_message():
    assert gates._attribution("fix(store): tidy the grant filter\n") is None


@pytest.mark.parametrize(
    "trailer",
    [
        "Co-Authored-By: Claude <noreply@anthropic.com>",
        "Claude-Session: https://claude.ai/code/session_1",
        "Signed-off-by: Claude <noreply@anthropic.com>",
        "Generated with Claude Code",
    ],
)
def test_attribution_names_the_trailer_it_finds(trailer):
    message = f"fix(store): tidy the grant filter\n\n{trailer}\n"
    assert gates._attribution(message) == f"attribution trailer: {trailer}"
