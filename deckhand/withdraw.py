"""A story taken off the board: closed as not planned, logged with why, and its item removed.

A draft that turned out to be a duplicate, or a story that will not be built, has no step: nothing
was built, so nothing finishes it. This is the one place that closes such an issue, and it refuses
built work, which is merged or has its pull request closed by hand.
"""

from __future__ import annotations

from deckhand import board, columns, fields, gh, issue, log
from deckhand.config import Settings
from deckhand.step import Refusal, issue_ref, ref_label

NOT_PLANNED = "not planned"
REF_FORM = "a story is owner/name#M, or M for this repository"


def _ref(value: str, repo: str) -> tuple[str, int]:
    try:
        return issue_ref(value, repo)
    except ValueError as error:
        raise Refusal(f"{REF_FORM}, got {value!r}") from error


def withdraw(settings: Settings, here: str, ref: str, note: str) -> None:
    """Close the story `ref` names as not planned, log `note`, and take its item off the board."""
    repo, number = _ref(ref, here)
    label = ref_label(repo, number, here)
    story = issue.view(repo, number)
    if issue.LABEL[0] not in story.labels:
        raise Refusal(f"{label} is not a deckhand story")
    closed = story.state.upper() == "CLOSED"
    if closed and story.state_reason not in issue.DROPPED:
        raise Refusal(f"{label} is closed as completed; finished work stays closed")
    status = fields.get_field(settings, repo, number, "Status")
    if status is not None and columns.at_least(status, columns.IN_REVIEW):
        built = "a story with a pull request is merged or its pull request is closed by hand"
        raise Refusal(f"{label} is {status}; {built}")
    waiting = issue.blocking(repo, number)
    if waiting:
        named = ", ".join(ref_label(where, found, here) for where, found, _ in waiting)
        raise Refusal(f"{named} wait on {label}; captain apply --unblock first")
    issue.comment(repo, number, log.checked(f"Withdrawn: {note}"))
    print("Logged Withdrawn", flush=True)
    if not closed:
        issue.close(repo, number, NOT_PLANNED)
        print(f"Closed {label} as {NOT_PLANNED}", flush=True)
    item = gh.item_id(settings, repo, number)
    if item is not None:
        board.remove(settings, item)
        print("Removed from the board", flush=True)
    print(f"Withdrawn {label}")
