"""The captain: the fleet, the order, the sessions, and the anomalies, in one printing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import cli

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "acme/widgets"


@pytest.fixture
def fleet_env(fake_gh, tmp_path, monkeypatch):
    blocked = tmp_path / "blocked.json"
    blocked.write_text(
        json.dumps({"257": [{"number": 253, "state": "open", "title": "Seed", "repository": {"full_name": REPO}}]})
    )
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-items.json"))
    monkeypatch.setenv("GH_BLOCKED_BY_MAP", str(blocked))
    monkeypatch.setenv("DECKHAND_SESSIONS", str(tmp_path / "sessions"))
    (tmp_path / "sessions").mkdir()
    return tmp_path


def test_a_context_prints_the_four_blocks(fleet_env, capsys):
    assert cli.main(["captain", "context"]) == 0
    out = capsys.readouterr().out
    assert "## Fleet" in out
    assert "## Order" in out
    assert "## Sessions" in out
    assert "## Anomalies" in out


def test_the_fleet_names_every_story_that_is_not_done(fleet_env, capsys):
    cli.main(["captain", "context"])
    out = capsys.readouterr().out
    assert "| 253 |" in out
    assert "Seed the role matrix" in out


def test_the_order_names_what_to_run_per_repository(fleet_env, capsys):
    cli.main(["captain", "context"])
    assert "Next per repository:" in capsys.readouterr().out


def test_no_sessions_is_a_line_and_not_a_missing_block(fleet_env, capsys):
    cli.main(["captain", "context"])
    out = capsys.readouterr().out
    assert "## Sessions" in out
    assert "none" in out


def test_a_context_never_fails(fake_gh, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_VIEW_FAILS", "1")
    assert cli.main(["captain", "context"]) == 0


def test_context_can_be_asked_for_one_block(fleet_env, capsys):
    """The skill reruns this for every later question, and four blocks is a whole fleet table each time."""
    assert cli.main(["captain", "context", "--only", "sessions"]) == 0
    out = capsys.readouterr().out
    assert "## Sessions" in out
    assert "## Fleet" not in out


def _session(root: Path, name: str, records: list[dict]) -> Path:
    path = root / "acme" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def test_a_named_session_prints_its_last_prompt_and_its_last_word(fleet_env, capsys):
    _session(
        fleet_env / "sessions",
        "one",
        [
            {"type": "user", "cwd": "/x/widgets", "message": {"content": "hi"}},
            {"type": "last-prompt", "lastPrompt": "proceed"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Which kind is it?"}]}},
        ],
    )
    assert cli.main(["captain", "context", "--session", "one"]) == 0
    out = capsys.readouterr().out
    assert "Last prompt: proceed" in out
    assert "Which kind is it?" in out


def test_a_session_id_nobody_has_says_so(fleet_env, capsys):
    assert cli.main(["captain", "context", "--session", "z"]) == 0
    assert "no session z" in capsys.readouterr().out


def test_an_apply_with_no_flag_refuses(fleet_env, capsys):
    assert cli.main(["captain", "apply"]) == 1
    assert "--order" in capsys.readouterr().err


def test_the_order_is_written_to_the_board(fleet_env, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--order"]) == 0
    calls = "\n".join(gh_calls())
    assert calls.count("updateProjectV2ItemPosition") == 4
    assert "Ordered 4 stories" in capsys.readouterr().out


def test_a_repair_sets_the_status_the_log_allows(fleet_env, capsys):
    assert cli.main(["captain", "apply", "--repair"]) == 0
    out = capsys.readouterr().out
    assert "268" in out and "Draft" in out


def test_a_repair_with_nothing_to_repair_refuses(fleet_env, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-clean.json"))
    assert cli.main(["captain", "apply", "--repair"]) == 1
    assert "nothing to repair" in capsys.readouterr().err


def test_blocking_a_boarded_story_records_the_dependency(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--block", "253", "--by", "117"]) == 0
    calls = "\n".join(gh_calls())
    assert "api -X POST repos/acme/widgets/issues/253/dependencies/blocked_by" in calls
    assert "#253 blocked by #117" in capsys.readouterr().out


def test_unblocking_a_boarded_story_drops_the_dependency(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--unblock", "253", "--by", "117"]) == 0
    calls = "\n".join(gh_calls())
    assert "api -X DELETE repos/acme/widgets/issues/253/dependencies/blocked_by" in calls
    assert "#253 no longer blocked by #117" in capsys.readouterr().out


def test_a_closed_blocker_refuses_and_writes_nothing(fake_gh, gh_calls, monkeypatch, tmp_path, capsys):
    closed = tmp_path / "issue-117-closed.json"
    closed.write_text(json.dumps({"number": 117, "state": "CLOSED", "body": ""}), encoding="utf-8")
    monkeypatch.setenv("GH_ISSUE_FILE_117", str(closed))

    assert cli.main(["captain", "apply", "--block", "253", "--by", "117"]) == 1

    assert capsys.readouterr().err == "deckhand captain apply: #117 is closed\n"
    assert not any("dependencies/blocked_by" in call for call in gh_calls())


def test_block_refuses_without_by(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--block", "253"]) == 1
    assert "--by" in capsys.readouterr().err
    assert gh_calls() == []


def test_a_story_cannot_block_itself(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--block", "253", "--by", "253"]) == 1
    assert "cannot block itself" in capsys.readouterr().err
    assert not any("dependencies/blocked_by" in call for call in gh_calls())


def test_block_and_unblock_together_refuses(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--block", "253", "--unblock", "253", "--by", "117"]) == 1
    assert "one at a time" in capsys.readouterr().err
    assert gh_calls() == []


def test_a_long_command_does_not_blow_out_the_session_table(fleet_env, capsys):
    _session(
        fleet_env / "sessions",
        "wordy",
        [
            {
                "type": "user",
                "cwd": "/x/widgets",
                "message": {
                    "content": "<command-name>/deckhand:new</command-name>\n"
                    "<command-args>the test refactor and idiomatic discussion we had in this session</command-args>"
                },
            }
        ],
    )
    cli.main(["captain", "context"])
    row = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("| word |"))
    assert "new the test refactor" in row
    assert len(row) < 110
