"""What a session has said, read from the tail of its transcript when a person asks about it."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from deckhand.sessions import ran, records_back, spoke, text

DEEP_LINES = 12
_SAID = 400
ASKING = 200
_SENTENCE = re.compile(r"(?<=[.?!])\s+")


@dataclass(frozen=True)
class Deep:
    """One session read properly, which is paid for only when a person asks about that session."""

    prompt: str
    lines: list[str]


def _parts(record: dict) -> list:
    content = (record.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def _questions(record: dict) -> tuple[str, set[str]]:
    """The questions the assistant asked with options in this record, and the ids of those calls."""
    said: list[str] = []
    ids: set[str] = set()
    for part in _parts(record):
        if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("name") == "AskUserQuestion":
            ids.add(str(part.get("id") or ""))
            for question in (part.get("input") or {}).get("questions") or []:
                labels = ", ".join(str(option.get("label") or "") for option in question.get("options") or [])
                said.append(f"{question.get('question') or ''} [{labels}]")
    return " ".join(said), ids


def _answer(record: dict, asked: set[str]) -> str:
    """The person's answer to one of the `asked` calls, when this record carries it."""
    for part in _parts(record):
        if isinstance(part, dict) and part.get("type") == "tool_result" and part.get("tool_use_id") in asked:
            body = part.get("content")
            if isinstance(body, str):
                return body
            return " ".join(str(block.get("text") or "") for block in body or [] if isinstance(block, dict))
    return ""


def _typed(record: dict) -> bool:
    """Whether a person typed this record; anything the assistant asked before it has had its answer."""
    content = (record.get("message") or {}).get("content")
    origin = record.get("origin")
    human = not isinstance(origin, dict) or origin.get("kind") == "human"
    return record.get("type") == "user" and isinstance(content, str) and not record.get("isMeta") and human


def _results(record: dict) -> set[str]:
    results = [part for part in _parts(record) if isinstance(part, dict) and part.get("type") == "tool_result"]
    return {str(part.get("tool_use_id")) for part in results}


def _permission(record: dict, done: set[str]) -> str:
    """`permission: <tool>` for a call still waiting on the person, other than a question."""
    for part in _parts(record):
        if not isinstance(part, dict) or part.get("type") != "tool_use" or str(part.get("id")) in done:
            continue
        name = str(part.get("name") or "")
        if name == "Bash":
            return f"permission: Bash {' '.join(ran(record).split())}"
        if name != "AskUserQuestion":
            return f"permission: {name}"
    return ""


def _last_sentence(said: str) -> str:
    return next((line for line in reversed(_SENTENCE.split(" ".join(said.split()))) if line), "")


def asking(path: Path) -> str:
    """What a waiting session is asking: an open question, a call it wants allowed, or its last sentence."""
    done: set[str] = set()
    newest = True
    try:
        for count, record in enumerate(records_back(path)):
            if count >= DEEP_LINES * 4 or _typed(record):
                break
            done |= _results(record)
            if record.get("type") != "assistant":
                continue
            if newest:
                newest = False
                allow = _permission(record, done)
                if allow:
                    return allow[:ASKING]
            question, ids = _questions(record)
            if question and not ids & done:
                return question[:ASKING]
            if spoke(record):
                return _last_sentence(text(record))[:ASKING]
    except OSError:
        return ""
    return ""


def deep(path: Path, limit: int = DEEP_LINES) -> Deep:
    """The tail of one transcript: the last prompt a person typed, and what has been said since.

    A question asked with options is a tool call rather than text, so it and its answer are read
    from the calls; tool traffic carries no text, which is why more records are read than lines kept.
    """
    prompt = ""
    records: list[dict] = []
    for record in records_back(path):
        if not prompt and record.get("type") == "last-prompt":
            prompt = " ".join(str(record.get("lastPrompt") or "").split())
        records.append(record)
        if prompt and len(records) >= limit * 4:
            break
    lines: list[str] = []
    asked: set[str] = set()
    for record in reversed(records):
        question, ids = _questions(record)
        asked |= ids
        answer = " ".join(_answer(record, asked).split())
        said = " ".join(text(record).split())
        for line in (
            f"asked: {question}" if question else "",
            f"answered: {answer}" if answer else "",
            f"{record.get('type')}: {said}" if said else "",
        ):
            if line:
                lines.append(line[:_SAID])
    return Deep(prompt=prompt, lines=lines[-limit:])
