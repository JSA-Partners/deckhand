from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from deckhand.questions import HEADER_PREFIX
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


def record(hook_input: dict, state: Path) -> None:
    response = hook_input.get("tool_response") or {}
    given = response.get("answers") or {}
    rows = []
    for item in response.get("questions") or []:
        header = item.get("header", "")
        if not header.startswith(HEADER_PREFIX):
            continue
        question = item.get("question", "")
        rows.append(
            {
                "at": time.time(),
                "session_id": hook_input.get("session_id", ""),
                "tool_use_id": hook_input.get("tool_use_id", ""),
                "header": header,
                "question": question,
                "answer": given.get(question, ""),
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


def find(nonce: str, state: Path) -> Answer | None:
    path = state / FILE
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise Refusal(f"{path} is readable by others, run: chmod 600 {path}")
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Refusal(f"cannot read {path}: {error.strerror}") from None
    found = None
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("header") == f"{HEADER_PREFIX}{nonce}":
            found = Answer(nonce, row.get("question", ""), row.get("answer", ""), row.get("at", 0.0))
    return found
