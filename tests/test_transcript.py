"""What a session has said, read from the tail of its transcript."""

from __future__ import annotations

import json
from pathlib import Path

from deckhand import transcript


def _write(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def test_a_deep_read_gives_the_last_prompt_and_the_last_word(tmp_path):
    records = [
        {"type": "user", "cwd": "/Users/x/acme/widgets", "message": {"content": "hi"}},
        {"type": "last-prompt", "lastPrompt": "proceed"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Which kind is it?"}]}},
    ]
    path = _write(tmp_path / "s.jsonl", records)
    read = transcript.deep(path, limit=5)
    assert read.prompt == "proceed"
    assert read.lines[-1] == "assistant: Which kind is it?"


def test_a_deep_read_shows_a_question_and_its_answer(tmp_path):
    """A question with options is a tool call, not text, so it was invisible to the captain."""
    question = {"question": "Split or fold?", "options": [{"label": "Split"}, {"label": "Fold"}]}
    asked = {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": "q1", "name": "AskUserQuestion", "input": {"questions": [question]}}]
        },
    }
    answered = {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "q1", "content": "Fold"}]},
    }
    path = _write(tmp_path / "s.jsonl", [asked, answered])

    assert transcript.deep(path, limit=5).lines == ["asked: Split or fold? [Split, Fold]", "answered: Fold"]


def test_a_deep_read_shows_a_question_still_waiting(tmp_path):
    question = {"question": "Board it?", "options": [{"label": "Yes"}, {"label": "Not yet"}]}
    asked = {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": "q2", "name": "AskUserQuestion", "input": {"questions": [question]}}]
        },
    }
    path = _write(tmp_path / "s.jsonl", [asked])

    assert transcript.deep(path, limit=5).lines == ["asked: Board it? [Yes, Not yet]"]


def _last_words(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def test_asking_names_the_last_question_with_its_options(tmp_path):
    question = {"question": "Board it?", "options": [{"label": "Board it"}, {"label": "Not yet"}]}
    asked = {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": "q3", "name": "AskUserQuestion", "input": {"questions": [question]}}]
        },
    }
    path = _write(tmp_path / "s.jsonl", [_last_words("Earlier words."), asked])

    assert transcript.asking(path) == "Board it? [Board it, Not yet]"


def test_asking_falls_back_to_the_last_sentence(tmp_path):
    path = _write(tmp_path / "s.jsonl", [_last_words("Merged.\nShould I start #261 next?")])

    assert transcript.asking(path) == "Should I start #261 next?"


def test_asking_is_cut_at_the_limit(tmp_path):
    path = _write(tmp_path / "s.jsonl", [_last_words("x" * 500)])

    assert len(transcript.asking(path)) == transcript.ASKING


def _asked(ask_id: str) -> dict:
    question = {"question": "Board it?", "options": [{"label": "Yes"}, {"label": "Not yet"}]}
    call = {"type": "tool_use", "id": ask_id, "name": "AskUserQuestion", "input": {"questions": [question]}}
    return {"type": "assistant", "message": {"content": [call]}}


def _result(call_id: str) -> dict:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "content": "ok"}]}}


def _call(name: str, **given) -> dict:
    call = {"type": "tool_use", "id": "t1", "name": name, "input": given}
    return {"type": "assistant", "message": {"content": [call]}}


def test_asking_stops_at_what_the_person_last_typed(tmp_path):
    typed = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "go on"}}
    path = _write(tmp_path / "s.jsonl", [_asked("q6"), typed])

    assert transcript.asking(path) == ""


def test_asking_reads_past_a_message_no_person_typed(tmp_path):
    handback = {"type": "user", "isMeta": True, "origin": {"kind": "peer"}, "message": {"content": "report"}}
    for injected in (handback, {**handback, "isMeta": False}, {**handback, "origin": {"kind": "human"}}):
        path = _write(tmp_path / "s.jsonl", [_asked("q7"), {"type": "system"}, injected])
        assert transcript.asking(path) == "Board it? [Yes, Not yet]", injected


def test_asking_reads_past_the_last_prompt_record(tmp_path):
    path = _write(
        tmp_path / "s.jsonl", [_last_words("Merged. Start #261?"), {"type": "last-prompt", "lastPrompt": "go"}]
    )

    assert transcript.asking(path) == "Start #261?"


def test_asking_skips_a_question_already_answered(tmp_path):
    path = _write(tmp_path / "s.jsonl", [_last_words("Merged. Pick the next one?"), _asked("q5"), _result("q5")])

    assert transcript.asking(path) == "Pick the next one?"


def test_asking_names_a_command_waiting_for_permission(tmp_path):
    path = _write(tmp_path / "s.jsonl", [_last_words("Running it."), _call("Bash", command="uv run\n pytest")])
    assert transcript.asking(path) == "permission: Bash uv run pytest"
    path = _write(tmp_path / "s.jsonl", [_last_words("Editing."), _call("Edit", file_path="a.py")])
    assert transcript.asking(path) == "permission: Edit"


def test_asking_reads_no_further_than_its_bound(tmp_path):
    traffic = [_result(f"t{index}") for index in range(transcript.DEEP_LINES * 4)]
    path = _write(tmp_path / "s.jsonl", [_last_words("Far back?"), *traffic])

    assert transcript.asking(path) == ""


def test_asking_a_vanished_transcript_is_blank(tmp_path):
    assert transcript.asking(tmp_path / "gone.jsonl") == ""
