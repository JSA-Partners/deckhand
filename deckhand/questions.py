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
    descriptions: tuple[str, ...] = ()


def build(kind: str, subject: str, text: str, options: list[str], descriptions: list[str] | None = None) -> Question:
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise ValueError(f"the question dialog shows {MIN_OPTIONS} to {MAX_OPTIONS} options, not {len(options)}")
    nonce = secrets.token_hex(4)
    said = tuple(descriptions or [""] * len(options))
    return Question(nonce, f"{HEADER_PREFIX}{nonce}", text, list(options), kind, subject, said)


def as_tool_input(q: Question) -> dict:
    descriptions = q.descriptions or ("",) * len(q.options)
    return {
        "questions": [
            {
                "question": q.text,
                "header": q.header,
                "options": [
                    {"label": label, "description": description}
                    for label, description in zip(q.options, descriptions, strict=True)
                ],
                "multiSelect": False,
            }
        ]
    }
