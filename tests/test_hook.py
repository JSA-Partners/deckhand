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


def test_hook_ask_allows_clean_deckhand_question(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", ["Wide", "Narrow"])
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", ["Wide", "Narrow"])) == 0
    assert capsys.readouterr().out == ""


def test_hook_ask_ignores_other_questions(monkeypatch, capsys):
    assert run(monkeypatch, ["ask"], asked("Scope", ["Wide (Recommended)"])) == 0
    assert capsys.readouterr().out == ""


def pending_for(state: Path, nonce: str, question: str, options: list[str]) -> None:
    folder = state / "pending"
    folder.mkdir(parents=True)
    (folder / f"{nonce}.json").write_text(
        json.dumps(
            {
                "nonce": nonce,
                "kind": "contract",
                "subject": "s",
                "version": "v",
                "stories": ["s1"],
                "question": question,
                "options": options,
                "descriptions": [],
            }
        )
    )


def test_hook_ask_denies_deckhand_question_without_pending(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", ["Wide", "Narrow"])) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("question,labels", [("Other?", ["Wide", "Narrow"]), ("Q?", ["Wide", "Wider"])])
def test_hook_ask_denies_a_question_that_differs_from_pending(monkeypatch, tmp_path: Path, capsys, question, labels):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", question, ["Wide", "Narrow"])
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", labels)) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_ask_allows_the_pending_question(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", ["Wide", "Narrow"])
    assert run(monkeypatch, ["ask"], asked("dh abcd1234", ["Wide", "Narrow"])) == 0
    assert capsys.readouterr().out == ""


def test_hook_ask_ignores_a_malformed_nonce_with_one_stderr_line(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    (tmp_path / "x.json").write_text("{}")
    assert run(monkeypatch, ["ask"], asked("dh ../x", ["Wide", "Narrow"])) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.splitlines()) == 1


def deny(capsys) -> bool:
    out = capsys.readouterr().out
    return bool(out) and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "option",
    [
        {"label": "Wide", "description": "The wider scope (Recommended)"},
        {"label": "Wide", "description": "Recommended for most teams"},
        {"label": "Recommended", "description": "contract: s"},
    ],
)
def test_hook_ask_denies_recommended_in_a_label_or_description(monkeypatch, tmp_path: Path, capsys, option):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", [option["label"], "Narrow"])
    payload = asked("dh abcd1234", [option["label"], "Narrow"])
    payload["tool_input"]["questions"][0]["options"][0] = option
    assert run(monkeypatch, ["ask"], payload) == 0
    assert deny(capsys)


def test_hook_ask_denies_a_deckhand_question_that_carries_answers(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", ["Wide", "Narrow"])
    payload = asked("dh abcd1234", ["Wide", "Narrow"])
    payload["tool_input"]["answers"] = {"Q?": "Wide"}
    assert run(monkeypatch, ["ask"], payload) == 0
    assert deny(capsys)


def test_recommended_in_a_story_title_does_not_deny(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", ["Approve", "Not yet"])
    title = "contract: ACME-1 Show recommended products"
    options = [{"label": label, "description": title} for label in ("Approve", "Not yet")]
    payload = asked("dh abcd1234", [])
    payload["tool_input"]["questions"][0]["options"] = options
    assert run(monkeypatch, ["ask"], payload) == 0
    assert capsys.readouterr().out == ""


def test_recommended_in_a_fixed_description_still_denies(monkeypatch, tmp_path: Path, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    pending_for(tmp_path, "abcd1234", "Q?", ["Approve", "Not yet"])
    options = [{"label": "Approve", "description": "recommended: ACME-1"}, {"label": "Not yet"}]
    payload = asked("dh abcd1234", [])
    payload["tool_input"]["questions"][0]["options"] = options
    assert run(monkeypatch, ["ask"], payload) == 0
    assert deny(capsys)
