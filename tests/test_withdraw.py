"""Withdraw: a story closed as not planned, logged, and taken off the board, and the refusals before any write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import cli

FIXTURES = Path(__file__).parent / "fixtures"
NOTE = "a duplicate of #249, which names the helper to change"


def _writes(gh_calls) -> list[str]:
    return [call for call in gh_calls() if call.startswith(("issue comment", "issue close", "project item-delete"))]


def _issue(tmp_path, monkeypatch, **fields) -> None:
    """Issue 248 as `fields` say, over the fixture's open, labelled story."""
    data = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
    data.update(fields)
    path = tmp_path / "issue-248.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("GH_ISSUE_FILE_248", str(path))


def _withdraw(*extra: str) -> int:
    return cli.main(["captain", "apply", "--withdraw", "248", "--note", NOTE, *extra])


def test_withdraw_logs_closes_as_not_planned_and_removes_the_item(fake_gh, gh_calls, tmp_path, monkeypatch, capsys):
    copy = tmp_path / "bodies"
    monkeypatch.setenv("GH_BODY_FILE_COPY", str(copy))

    assert _withdraw() == 0

    writes = _writes(gh_calls)
    assert writes[0].startswith("issue comment 248 --repo acme/widgets --body-file")
    assert writes[1:] == [
        "issue close 248 --repo acme/widgets --reason not planned",
        "project item-delete 2 --owner acme --id PVTI_TEST_248",
    ]
    assert f"Withdrawn: {NOTE}" in copy.read_text(encoding="utf-8")
    assert capsys.readouterr().out == (
        "Logged Withdrawn\nClosed #248 as not planned\nRemoved from the board\nWithdrawn #248\n"
    )


def test_a_story_closed_as_not_planned_by_hand_still_leaves_the_board(fake_gh, gh_calls, tmp_path, monkeypatch, capsys):
    _issue(tmp_path, monkeypatch, state="CLOSED", stateReason="NOT_PLANNED")

    assert _withdraw() == 0

    assert [call.split(" --", 1)[0] for call in _writes(gh_calls)] == ["issue comment 248", "project item-delete 2"]
    assert "Closed" not in capsys.readouterr().out


def test_a_story_off_the_board_is_closed_and_logged_only(fake_gh, gh_calls, tmp_path, monkeypatch, capsys):
    none = tmp_path / "none.json"
    none.write_text('{"data":{"repository":{"issue":{"projectItems":{"nodes":[]}}}}}')
    monkeypatch.setenv("GH_GRAPHQL_FILE", str(none))

    assert _withdraw() == 0

    assert [call.split(" --", 1)[0] for call in _writes(gh_calls)] == ["issue comment 248", "issue close 248"]
    assert "Removed" not in capsys.readouterr().out


def test_a_story_in_another_repository_is_named_in_full(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--withdraw", "acme/gadgets#248", "--note", NOTE]) == 0

    assert "issue close 248 --repo acme/gadgets --reason not planned" in gh_calls()
    assert capsys.readouterr().out.endswith("Withdrawn acme/gadgets#248\n")


@pytest.mark.parametrize(
    ("fields", "refusal"),
    [
        ({"labels": []}, "#248 is not a deckhand story"),
        ({"state": "CLOSED", "stateReason": "COMPLETED"}, "#248 is closed as completed; finished work stays closed"),
    ],
)
def test_an_issue_that_is_not_an_open_story_refuses(fake_gh, gh_calls, tmp_path, monkeypatch, capsys, fields, refusal):
    _issue(tmp_path, monkeypatch, **fields)

    assert _withdraw() == 1

    assert capsys.readouterr().err == f"deckhand captain apply: {refusal}\n"
    assert _writes(gh_calls) == []


def test_a_story_with_a_pull_request_refuses(fake_gh, gh_calls, tmp_path, monkeypatch, capsys):
    data = json.loads((FIXTURES / "graphql-fieldvalues.json").read_text(encoding="utf-8"))
    for node in data["data"]["node"]["fieldValues"]["nodes"]:
        if (node.get("field") or {}).get("name") == "Status":
            node["name"] = "In Review"
    path = tmp_path / "in-review.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("GH_FIELDVALUES_FILE", str(path))

    assert _withdraw() == 1

    assert capsys.readouterr().err == (
        "deckhand captain apply: #248 is In Review; a story with a pull request is merged or its pull request"
        " is closed by hand\n"
    )
    assert _writes(gh_calls) == []


def test_a_story_others_wait_on_refuses_and_names_them(fake_gh, gh_calls, monkeypatch, capsys):
    waiting = [
        {"number": 250, "title": "Later", "state": "open", "repository": {"full_name": "acme/widgets"}},
        {"number": 7, "title": "Screen", "state": "open", "repository": {"full_name": "acme/gadgets"}},
    ]
    monkeypatch.setenv("GH_BLOCKING", json.dumps(waiting))

    assert _withdraw() == 1

    assert capsys.readouterr().err == (
        "deckhand captain apply: acme/gadgets#7, #250 wait on #248; captain apply --unblock first\n"
    )
    assert _writes(gh_calls) == []


def test_withdraw_refuses_without_a_note(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--withdraw", "248"]) == 1

    assert capsys.readouterr().err == "deckhand captain apply: --withdraw needs --note, saying why\n"
    assert gh_calls() == []


def test_a_malformed_reference_refuses(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--withdraw", "widgets-248", "--note", NOTE]) == 1

    assert capsys.readouterr().err == (
        "deckhand captain apply: a story is owner/name#M, or M for this repository, got 'widgets-248'\n"
    )
    assert _writes(gh_calls) == []
