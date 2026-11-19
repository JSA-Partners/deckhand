from __future__ import annotations

from dataclasses import replace

from deckhand import gaps
from deckhand.records import HAND, Record, content_hash


def test_record_names_the_gap_and_cuts_the_command():
    command = "npm install " + "x" * 300
    found = gaps.record("s1", "PermissionRequest", "Bash", command, "not allowed")
    digest = content_hash("\0".join(("PermissionRequest", "Bash", command)))
    assert (found.kind, found.subject, found.author) == ("permission-gap", f"s1:{digest[:12]}", HAND)
    assert found.data == {
        "event": "PermissionRequest",
        "tool": "Bash",
        "command": command[:200],
        "reason": "not allowed",
    }


def test_same_gap_same_subject_and_another_command_another():
    one = gaps.record("s1", "PermissionDenied", "Bash", "curl example.test", "")
    assert one.subject == gaps.record("s1", "PermissionDenied", "Bash", "curl example.test", "again").subject
    assert one.subject != gaps.record("s1", "PermissionDenied", "Bash", "curl other.test", "").subject


def test_latest_gap():
    old = replace(gaps.record("s1", "PermissionRequest", "Bash", "a", ""), at="2026-11-19T10:00:00.000Z")
    new = replace(gaps.record("s1", "PermissionRequest", "Bash", "b", ""), at="2026-11-19T11:00:00.000Z")
    other = Record("hand-question", "s1:q", "v", HAND, {}, at="2026-11-19T12:00:00.000Z")
    assert gaps.latest([new, other, old]) == new
    assert gaps.latest([other]) is None
