from __future__ import annotations

import secrets
from dataclasses import dataclass

HEADER_PREFIX = "dh "


@dataclass(frozen=True)
class Question:
    nonce: str
    header: str
    text: str
    options: list[str]
    multi: bool
    kind: str = ""
    subject: str = ""


def build(kind: str, subject: str, text: str, options: list[str], multi: bool = False) -> Question:
    nonce = secrets.token_hex(4)
    return Question(nonce, f"{HEADER_PREFIX}{nonce}", text, list(options), multi, kind, subject)


def as_tool_input(q: Question) -> dict:
    description = f"{q.kind}: {q.subject}"
    return {
        "questions": [
            {
                "question": q.text,
                "header": q.header,
                "options": [{"label": label, "description": description} for label in q.options],
                "multiSelect": q.multi,
            }
        ]
    }
