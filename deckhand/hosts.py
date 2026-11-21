from __future__ import annotations

import json
import shlex
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

WIDGET = "deckhand-captain"
SEPARATOR = f"{WIDGET}-separator"
HUD_OPT_IN = "CLAUDE_HUD_ALLOW_EXTRA_CMD=1"
HUD_ENTRIES = ("statusline.mjs", "index.js")
OPERATORS = set("();<>|&")


def owner(command: str) -> str:
    if not command.strip():
        return "none"
    for name, words in (
        ("claude-hud", ("claude-hud",)),
        ("ccstatusline", ("ccstatusline",)),
        ("ccometixline", ("ccline", "ccometixline")),
    ):
        if any(word in command for word in words):
            return name
    return "other"


def dump(data: Any, like: bytes | None) -> bytes:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    return (text + ("\n" if like is None or like.endswith(b"\n") else "")).encode()


def _simple(command: str) -> bool:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return not any(set(token) <= OPERATORS for token in lexer)
    except ValueError:
        return False


def hud(command: str, extra: str) -> str | None:
    arg = shlex.quote(extra)
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    trimmed = command.rstrip()
    if tokens and tokens[-1].endswith(HUD_ENTRIES) and "-c" not in tokens and _simple(command):
        edited = f"{HUD_OPT_IN} {trimmed} --extra-cmd {arg}"
        expected = [HUD_OPT_IN, *tokens, "--extra-cmd", extra]
    elif (
        len(tokens) == 3
        and tokens[1] == "-c"
        and tokens[2].rstrip().rstrip('"').endswith(HUD_ENTRIES)
        and trimmed.endswith("'")
    ):
        inner = arg.replace("'", "'\"'\"'")
        edited = f"{HUD_OPT_IN} {trimmed[:-1]} --extra-cmd {inner}'"
        expected = [HUD_OPT_IN, tokens[0], "-c", f"{tokens[2]} --extra-cmd {arg}"]
    else:
        return None
    try:
        return edited if shlex.split(edited) == expected else None
    except ValueError:
        return None


def ccstatusline_file(command: str, default: Path) -> Path | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    for index, token in enumerate(tokens):
        value = tokens[index + 1] if token == "--config" and index + 1 < len(tokens) else ""
        value = token.removeprefix("--config=") if token.startswith("--config=") else value
        if value:
            path = Path(value).expanduser()
            return path if path.is_absolute() else None
    return default


def widget(raw: bytes | None, segment: str, ours: Callable[[str], bool]) -> tuple[bool, bytes | None]:
    try:
        data = json.loads(raw.decode("utf-8-sig")) if raw else None
    except ValueError:
        data = None
    lines = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(lines, list) or not lines or not all(isinstance(row, list) for row in lines):
        return False, None
    items = [item for row in lines for item in row if isinstance(item, dict)]
    if any(item.get("id") == WIDGET or ours(str(item.get("commandPath", ""))) for item in items):
        return True, None
    new = {"id": WIDGET, "type": "custom-command", "commandPath": segment}
    empty = next((row for row in lines if not row), None)
    if empty is not None:
        empty.append(new)
    elif len(lines) < 3:
        lines.append([new])
    else:
        lines[-1] += [{"id": SEPARATOR, "type": "separator"}, new]
    return False, dump(data, raw)


def drop_widget(data: dict[str, Any], raw: bytes) -> bytes:
    lines = data.get("lines") if isinstance(data.get("lines"), list) else []
    ids = (WIDGET, SEPARATOR)
    keep = [[item for item in row if not (isinstance(item, dict) and item.get("id") in ids)] for row in lines]
    return dump({**data, "lines": keep}, raw)


def ccline_block(segment: str) -> str:
    run = json.dumps(segment, ensure_ascii=False)
    return f'\n[[line]]\nleft = [{{ metric = "cmd", options = {{ run = {run}, cache_s = 5 }} }}]\n'


def cmd_item(raw: bytes | None, segment: str, ours: Callable[[str], bool]) -> tuple[bool, bytes | None]:
    if raw is None:
        return False, None
    try:
        text = raw.decode()
        lines = tomllib.loads(text).get("line")
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False, None
    if not isinstance(lines, list) or not lines:
        return False, None
    if ours(text):
        return True, None
    edited = text + ("" if text.endswith("\n") else "\n") + ccline_block(segment)
    item = {"metric": "cmd", "options": {"run": segment, "cache_s": 5}}
    try:
        valid = tomllib.loads(edited).get("line") == [*lines, {"left": [item]}]
    except tomllib.TOMLDecodeError:
        valid = False
    return False, edited.encode() if valid else None
