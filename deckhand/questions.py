from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

HEADER_PREFIX = "dh "
MIN_OPTIONS, MAX_OPTIONS = 2, 4
NONCE = re.compile(r"[0-9a-f]{8}")
RECOMMENDED = re.compile(r"\brecommended\b", re.IGNORECASE)


def valid_nonce(nonce: str) -> bool:
    return NONCE.fullmatch(nonce) is not None


@dataclass(frozen=True)
class Question:
    nonce: str
    header: str
    text: str
    options: list[str]
    kind: str = ""
    subject: str = ""


def build(kind: str, subject: str, text: str, options: list[str]) -> Question:
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise ValueError(f"the question dialog shows {MIN_OPTIONS} to {MAX_OPTIONS} options, not {len(options)}")
    nonce = secrets.token_hex(4)
    return Question(nonce, f"{HEADER_PREFIX}{nonce}", text, list(options), kind, subject)


def as_tool_input(q: Question) -> dict:
    description = f"{q.kind}: {q.subject}"
    return {
        "questions": [
            {
                "question": q.text,
                "header": q.header,
                "options": [{"label": label, "description": description} for label in q.options],
                "multiSelect": False,
            }
        ]
    }
