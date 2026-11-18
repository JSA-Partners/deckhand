from __future__ import annotations

import json
import os
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from deckhand.questions import HEADER_PREFIX, valid_nonce
from deckhand.step import Refusal

FILE = "answers.jsonl"


def state_dir() -> Path:
    return Path(os.environ.get("DECKHAND_STATE_DIR") or Path.home() / ".local" / "state" / "deckhand")


@dataclass(frozen=True)
class Answer:
    nonce: str
    question: str
    answer: str
    at: float
    session_id: str = ""
    copied: bool = False
    options: list[dict] | None = None
    multi: bool | None = None


MIN_COPIED = 20
RECENT = 5


def _recent_assistant_text(path: str) -> str:
    texts = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") != "assistant":
                continue
            content = (row.get("message") or {}).get("content") or []
            text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
            if text:
                texts.append(text)
    return "\n".join(texts[-RECENT:])


def only_labels(answer: str, labels: list[str]) -> bool:
    return any(
        answer == label or (answer.startswith(label + ", ") and only_labels(answer[len(label) + 2 :], labels))
        for label in labels
    )


def _is_copied(item: dict, answer: str, hook_input: dict) -> bool:
    labels = [str(o.get("label", "")) for o in item.get("options") or []]
    if len(answer) < MIN_COPIED or only_labels(answer, labels):
        return False
    try:
        return answer in _recent_assistant_text(hook_input.get("transcript_path") or "")
    except (OSError, ValueError, RecursionError, TypeError, AttributeError):
        print("deckhand: hook answer could not read the transcript, copied check skipped", file=sys.stderr)
        return False


def record(hook_input: dict, state: Path) -> None:
    response = hook_input.get("tool_response") or {}
    given = response.get("answers") or {}
    rows = []
    for item in response.get("questions") or []:
        header = item.get("header", "")
        if not header.startswith(HEADER_PREFIX):
            continue
        if not valid_nonce(header[len(HEADER_PREFIX) :]):
            print("deckhand: hook answer ignored a deckhand header with a malformed nonce", file=sys.stderr)
            continue
        question = item.get("question", "")
        if question not in given:
            continue
        rows.append(
            {
                "at": time.time(),
                "session_id": hook_input.get("session_id", ""),
                "tool_use_id": hook_input.get("tool_use_id", ""),
                "header": header,
                "question": question,
                "answer": given[question],
                "copied": _is_copied(item, str(given[question]), hook_input),
                "options": [
                    {"label": o.get("label"), "description": o.get("description")} for o in item.get("options") or []
                ],
                "multiSelect": item.get("multiSelect"),
            }
        )
    if not rows:
        return
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    fd = os.open(state / FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def check_nonce(nonce: str) -> None:
    if not valid_nonce(nonce):
        raise Refusal(f"{nonce!r} is not a deckhand nonce, eight lowercase hexadecimal digits")


def find(nonce: str, question: str, state: Path) -> Answer | None:
    check_nonce(nonce)
    path = state / FILE
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise Refusal(f"{path} is readable by others, run: chmod 600 {path}")
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Refusal(f"cannot read {path}: {error.strerror}") from None
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("header") == f"{HEADER_PREFIX}{nonce}":
            if row.get("question") != question:
                raise Refusal(f"the recorded question for {nonce} differs from the one asked")
            return Answer(
                nonce,
                question,
                row.get("answer", ""),
                row.get("at", 0.0),
                row.get("session_id", ""),
                bool(row.get("copied", False)),
                row.get("options"),
                row.get("multiSelect"),
            )
    return None
