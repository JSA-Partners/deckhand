"""Every session working on a repository the board holds, read from the tail of its transcript."""

from __future__ import annotations

import json
from pathlib import Path

from deckhand import sessions


def _write(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def test_records_come_back_newest_first(tmp_path):
    path = _write(tmp_path / "s.jsonl", [{"n": 1}, {"n": 2}, {"n": 3}])
    assert [record["n"] for record in sessions.records_back(path)] == [3, 2, 1]


def test_a_blank_line_is_not_a_record(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"n": 1}\n\n{"n": 2}\n', encoding="utf-8")
    assert [record["n"] for record in sessions.records_back(path)] == [2, 1]


def test_a_record_longer_than_a_chunk_is_read_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "CHUNK", 8)
    path = _write(tmp_path / "s.jsonl", [{"n": 1, "pad": "x" * 200}, {"n": 2}])
    assert [record["n"] for record in sessions.records_back(path)] == [2, 1]


REPOS = {"widgets": "acme/widgets"}


def _transcript(tmp_path: Path, **kwargs) -> Path:
    cwd = kwargs.get("cwd", "/Users/x/acme/widgets")
    records = [
        {"type": "user", "timestamp": "2026-09-16T10:00:00Z", "cwd": cwd, "message": {"content": "hi"}},
        {"type": "cost-state", "totalCostUSD": kwargs.get("cost", 12.4), "startTime": 1789581476942},
        {
            "type": "user",
            "message": {"content": "<command-name>/deckhand:next</command-name>\n<command-args>253</command-args>"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-16T10:05:00Z",
            "cwd": cwd,
            "gitBranch": kwargs.get("branch", "main"),
            "message": {"content": [{"type": "text", "text": "Which kind is it?"}]},
        },
    ]
    return _write(tmp_path / "projects" / "acme" / "abcd1234.jsonl", records + kwargs.get("extra", []))


def test_a_pulse_names_the_repository_the_story_and_the_cost(tmp_path):
    pulse = sessions.pulse(_transcript(tmp_path), REPOS, now=0.0)
    assert (pulse.repo, pulse.story, pulse.command) == ("acme/widgets", "253", "next 253")
    assert pulse.cost == 12.4
    assert pulse.waiting is True


def test_a_session_outside_every_repository_is_not_a_pulse(tmp_path):
    assert sessions.pulse(_transcript(tmp_path, cwd="/Users/x/somewhere/else"), REPOS, now=0.0) is None


def test_a_worktree_belongs_to_the_repository_above_it(tmp_path):
    path = _transcript(tmp_path, cwd="/Users/x/acme/widgets/.claude/worktrees/feat-253-seed")
    assert sessions.pulse(path, REPOS, now=0.0).repo == "acme/widgets"


def test_a_session_that_ran_no_deckhand_command_is_free(tmp_path):
    records = [
        {
            "type": "user",
            "timestamp": "2026-09-16T10:00:00Z",
            "cwd": "/Users/x/acme/widgets",
            "message": {"content": "hi"},
        }
    ]
    path = _write(tmp_path / "projects" / "acme" / "ffff0000.jsonl", records)
    pulse = sessions.pulse(path, REPOS, now=0.0)
    assert (pulse.story, pulse.command, pulse.cost) == (sessions.FREE, "-", 0.0)


def test_a_branch_names_the_story_when_no_command_did(tmp_path):
    records = [
        {
            "type": "user",
            "timestamp": "2026-09-16T10:00:00Z",
            "cwd": "/Users/x/acme/widgets",
            "gitBranch": "feat-117-warn",
            "message": {"content": "hi"},
        }
    ]
    path = _write(tmp_path / "projects" / "acme" / "eeee0000.jsonl", records)
    assert sessions.pulse(path, REPOS, now=0.0).story == "117"


def test_a_session_whose_last_word_was_a_tool_result_is_not_waiting(tmp_path):
    extra = [
        {
            "type": "user",
            "timestamp": "2026-09-16T10:06:00Z",
            "message": {"content": [{"type": "tool_result", "text": "ok"}]},
        }
    ]
    assert sessions.pulse(_transcript(tmp_path, extra=extra), REPOS, now=0.0).waiting is False
