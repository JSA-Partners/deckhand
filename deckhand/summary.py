from __future__ import annotations

import json
from dataclasses import dataclass

from deckhand.step import Refusal

SHAPE = {
    "intents": 'a list of {"title", "lines"}, lines a list of strings',
    "new_here": 'a list of {"text", "path"}, what the change does that the repository does not yet',
    "screenshots": "a list of image paths",
    "state_diagram": "a path, or null",
}


@dataclass(frozen=True)
class Intent:
    title: str
    lines: list[str]


@dataclass(frozen=True)
class NewHere:
    text: str
    path: str


@dataclass(frozen=True)
class ChangeSummary:
    intents: list[Intent]
    new_here: list[NewHere]
    screenshots: list[str]
    state_diagram: str | None


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Refusal(f"{where} must be a non-empty string")
    return value.strip()


def _path(value: object, where: str) -> str:
    path = _text(value, where).removeprefix("./")
    if path.startswith("/") or ".." in path.split("/"):
        raise Refusal(f"{where} must be a relative path without .., not {path!r}")
    return path


def _entries(document: dict, name: str, fields: tuple[str, ...]) -> list[dict]:
    value = document[name]
    if not isinstance(value, list):
        raise Refusal(f"summary field {name} must be a list")
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise Refusal(f"{name} entry {index} must be an object")
        for field in entry:
            if field not in fields:
                raise Refusal(f"{name} entry {index} has an unknown field {field}")
    return value


def _lines(entry: dict, index: int) -> list[str]:
    lines = entry.get("lines")
    if not isinstance(lines, list):
        raise Refusal(f"intents entry {index} field lines must be a list of non-empty strings")
    return [_text(line, f"intents entry {index} field lines") for line in lines]


def parse_summary(text: str) -> ChangeSummary:
    try:
        document = json.loads(text)
    except ValueError:
        raise Refusal("the change summary is not valid JSON") from None
    if not isinstance(document, dict):
        raise Refusal("the change summary must be a JSON object")
    for name in document:
        if name not in SHAPE:
            raise Refusal(f"the change summary has an unknown field {name}")
    for name in SHAPE:
        if name not in document:
            raise Refusal(f"summary field {name} is missing")
    intents = [
        Intent(_text(entry.get("title"), f"intents entry {index} field title"), _lines(entry, index))
        for index, entry in enumerate(_entries(document, "intents", ("title", "lines")))
    ]
    new_here = [
        NewHere(
            _text(entry.get("text"), f"new_here entry {index} field text"),
            _path(entry.get("path"), f"new_here entry {index} field path"),
        )
        for index, entry in enumerate(_entries(document, "new_here", ("text", "path")))
    ]
    shots = document["screenshots"]
    if not isinstance(shots, list):
        raise Refusal("summary field screenshots must be a list")
    screenshots = [_text(shot, f"screenshots entry {index}") for index, shot in enumerate(shots)]
    diagram = document["state_diagram"]
    if diagram is not None:
        diagram = _text(diagram, "summary field state_diagram")
    return ChangeSummary(intents, new_here, screenshots, diagram)
