from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from deckhand import answers
from deckhand.step import Refusal

SPIKE_RESPONSE = {
    "questions": [
        {
            "question": "Spike 1, second question: type anything in Other.",
            "header": "Spike 1",
            "options": [
                {"label": "Gamma", "description": "Option Gamma."},
                {"label": "Delta", "description": "Option Delta."},
            ],
            "multiSelect": False,
        }
    ],
    "answers": {"Spike 1, second question: type anything in Other.": "Omicron"},
    "annotations": {},
}


def hook_input(*pairs: tuple[str, str, str]) -> dict:
    return {
        "session_id": "s1",
        "tool_use_id": "t1",
        "tool_response": {
            "questions": [{"question": q, "header": h} for h, q, _ in pairs],
            "answers": {q: a for _, q, a in pairs},
        },
    }


def test_record_keeps_only_deckhand_questions(tmp_path: Path):
    answers.record(hook_input(("dh abcd1234", "Pick?", "Alpha"), ("Other", "Free?", "x")), tmp_path)
    lines = (tmp_path / answers.FILE).read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["header"] == "dh abcd1234"
    assert row["answer"] == "Alpha"
    assert row["session_id"] == "s1" and row["tool_use_id"] == "t1"


def test_record_creates_owner_only_file(tmp_path: Path):
    state = tmp_path / "state"
    answers.record(hook_input(("dh abcd1234", "Pick?", "Alpha")), state)
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE((state / answers.FILE).stat().st_mode) == 0o600


def test_find_returns_typed_other_answer(tmp_path: Path):
    answers.record(hook_input(("dh abcd1234", "Pick?", "Omicron")), tmp_path)
    found = answers.find("abcd1234", "Pick?", tmp_path)
    assert found is not None
    assert found.answer == "Omicron" and found.question == "Pick?"


def test_find_returns_none_without_match(tmp_path: Path):
    assert answers.find("abcd1234", "Pick?", tmp_path) is None
    answers.record(hook_input(("dh 11111111", "Pick?", "A")), tmp_path)
    assert answers.find("abcd1234", "Pick?", tmp_path) is None


def test_find_refuses_world_readable_file(tmp_path: Path):
    answers.record(hook_input(("dh abcd1234", "Pick?", "A")), tmp_path)
    path = tmp_path / answers.FILE
    path.chmod(0o644)
    with pytest.raises(Refusal, match=str(path)):
        answers.find("abcd1234", "Pick?", tmp_path)


def test_find_uses_only_the_first_row_for_a_nonce(tmp_path: Path):
    answers.record(hook_input(("dh abcd1234", "Pick?", "Alpha")), tmp_path)
    answers.record(hook_input(("dh abcd1234", "Pick?", "Beta")), tmp_path)
    assert answers.find("abcd1234", "Pick?", tmp_path).answer == "Alpha"


def test_find_refuses_when_the_recorded_question_differs(tmp_path: Path):
    answers.record(hook_input(("dh abcd1234", "Reworded?", "Alpha")), tmp_path)
    with pytest.raises(Refusal, match="abcd1234"):
        answers.find("abcd1234", "Pick?", tmp_path)


def test_record_skips_a_question_without_an_answer(tmp_path: Path):
    payload = hook_input(("dh abcd1234", "Pick?", "x"))
    payload["tool_response"]["answers"] = {}
    answers.record(payload, tmp_path)
    assert answers.find("abcd1234", "Pick?", tmp_path) is None


def test_captured_spike_response_round_trips(tmp_path: Path):
    response = json.loads(json.dumps(SPIKE_RESPONSE))
    response["questions"][0]["header"] = "dh 0a1b2c3d"
    answers.record({"session_id": "s", "tool_use_id": "t", "tool_response": response}, tmp_path)
    found = answers.find("0a1b2c3d", SPIKE_RESPONSE["questions"][0]["question"], tmp_path)
    assert found.answer == "Omicron"


def transcript(tmp_path: Path, texts: list[str]) -> Path:
    path = tmp_path / "t.jsonl"
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}, {"type": "tool_use"}]}}
        for text in texts
    ]
    lines.insert(0, {"type": "user", "message": {"content": "hello"}})
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


def copied_of(state: Path) -> bool:
    return json.loads((state / answers.FILE).read_text().splitlines()[0])["copied"]


LONG = "a long free text answer that is copied"


def test_free_text_found_in_recent_assistant_text_is_marked_copied(tmp_path: Path):
    state = tmp_path / "state"
    payload = hook_input(("dh abcd1234", "Pick?", LONG))
    payload["transcript_path"] = str(transcript(tmp_path, ["before " + LONG + " after"]))
    answers.record(payload, state)
    assert copied_of(state) is True
    assert answers.find("abcd1234", "Pick?", state).copied is True


def test_free_text_only_in_an_older_assistant_message_is_not_copied(tmp_path: Path):
    state = tmp_path / "state"
    payload = hook_input(("dh abcd1234", "Pick?", LONG))
    payload["transcript_path"] = str(transcript(tmp_path, [LONG, "a", "b", "c", "d", "e"]))
    answers.record(payload, state)
    assert copied_of(state) is False


def test_short_or_offered_answers_are_not_copied(tmp_path: Path):
    state = tmp_path / "state"
    payload = hook_input(("dh abcd1234", "Pick?", "Approve"))
    payload["tool_response"]["questions"][0]["options"] = [{"label": "Approve"}]
    payload["transcript_path"] = str(transcript(tmp_path, ["Approve Approve Approve Approve"]))
    answers.record(payload, state)
    assert copied_of(state) is False


def test_unreadable_transcript_stores_not_copied_and_one_stderr_line(tmp_path: Path, capsys):
    state = tmp_path / "state"
    payload = hook_input(("dh abcd1234", "Pick?", LONG))
    payload["transcript_path"] = str(tmp_path / "missing.jsonl")
    answers.record(payload, state)
    assert copied_of(state) is False
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_invalid_utf8_in_the_transcript_still_records_the_answer(tmp_path: Path):
    state = tmp_path / "state"
    path = tmp_path / "bad.jsonl"
    path.write_bytes(b"\xff\xfe not json\n")
    payload = hook_input(("dh abcd1234", "Pick?", LONG))
    payload["transcript_path"] = str(path)
    answers.record(payload, state)
    assert answers.find("abcd1234", "Pick?", state).copied is False


def test_deeply_nested_transcript_still_records_the_answer(tmp_path: Path):
    state = tmp_path / "state"
    path = tmp_path / "deep.jsonl"
    path.write_text("[" * 100000 + "]" * 100000 + "\n")
    payload = hook_input(("dh abcd1234", "Pick?", LONG))
    payload["transcript_path"] = str(path)
    answers.record(payload, state)
    assert answers.find("abcd1234", "Pick?", state).copied is False


def test_tool_use_only_rows_do_not_use_up_the_window(tmp_path: Path):
    state = tmp_path / "state"
    path = tmp_path / "t.jsonl"
    rows = [{"type": "assistant", "message": {"content": [{"type": "text", "text": LONG}]}}]
    rows += [{"type": "assistant", "message": {"content": [{"type": "tool_use"}]}}] * 6
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    payload = hook_input(("dh abcd1234", "Pick?", LONG))
    payload["transcript_path"] = str(path)
    answers.record(payload, state)
    assert copied_of(state) is True
