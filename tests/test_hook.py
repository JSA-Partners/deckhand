from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from deckhand import answers, cli


def run(monkeypatch: pytest.MonkeyPatch, args: list[str], payload: object) -> int:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    return cli.main(["hook", "apply", *args])


def asked(header: str, labels: list[str]) -> dict:
    return {
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": "Q?", "header": header, "options": [{"label": x} for x in labels]}]},
    }


def test_hook_answer_records(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    payload = {
        "session_id": "s",
        "tool_use_id": "t",
        "tool_response": {"questions": [{"question": "Q?", "header": "dh abcd1234"}], "answers": {"Q?": "A"}},
    }
    assert run(monkeypatch, ["answer"], payload) == 0
    assert answers.find("abcd1234", "Q?", tmp_path).answer == "A"


def test_hook_answer_never_fails_the_session(monkeypatch, tmp_path: Path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(blocker / "state"))
    payload = {
        "tool_response": {
            "questions": [{"question": "SECRET?", "header": "dh abcd1234"}],
            "answers": {"SECRET?": "hunter2"},
        }
    }
    assert run(monkeypatch, ["answer"], payload) == 0
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    assert "hunter2" not in err and "SECRET" not in err
    assert run(monkeypatch, ["answer"], "not json") == 0


def test_hook_ask_denies_recommended_in_deckhand_questions(monkeypatch, capsys):
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", ["Wide (Recommended)", "Narrow"])) == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    assert out["permissionDecisionReason"]


def test_hook_ask_allows_clean_deckhand_question(monkeypatch, capsys):
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", ["Wide", "Narrow"])) == 0
    assert capsys.readouterr().out == ""


def test_hook_ask_ignores_other_questions(monkeypatch, capsys):
    assert run(monkeypatch, ["ask"], asked("Scope", ["Wide (Recommended)"])) == 0
    assert capsys.readouterr().out == ""
