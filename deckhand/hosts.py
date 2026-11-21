from __future__ import annotations

import json
import re
import shlex
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

MARK = "DECKHAND_STATUSLINE=1"
LAUNCHER = "deckhand"
GUARD = "DECKHAND_STATUSLINE_WRAPPED"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
WIDGET = "deckhand-captain"
SEPARATOR = f"{WIDGET}-separator"
HUD_OPT_IN = "CLAUDE_HUD_ALLOW_EXTRA_CMD=1"
HUD_ENTRIES = ("statusline.mjs", "index.js")
OPERATORS = set("();<>|&")


NEWEST = r"""versions=@VERSIONS@
version=$(ls "$versions" 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' \
  | while read -r name; do
      [ -e "$versions/$name/.orphaned_at" ] || [ ! -x "$versions/$name/bin/deckhand" ] || echo "$name"
    done \
  | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)
[ -n "$version" ] || { @MISSING@; }
bin="$versions/$version/bin/deckhand"
"""
QUIET = "exit 0"
MISSING = 'echo "deckhand: no installed deckhand plugin was found" >&2; exit 1'
PASS = 'exec "$bin" "$@"\n'


BOUNDED = r"""out=$(mktemp "${TMPDIR:-/tmp}/deckhand-segment.XXXXXX") || exit 0
"$bin" captain context --line "$@" </dev/null >"$out" 2>/dev/null &
pid=$!
(sleep 1; kill "$pid"; sleep 1; kill -9 "$pid") >/dev/null 2>&1 &
watch=$!
if wait "$pid" 2>/dev/null; then cat "$out"; fi
kill "$watch" 2>/dev/null
rm -f "$out"
exit 0
"""

WRAP = r"""#!/bin/sh
[ -n "$@GUARD@" ] && exit 0
@GUARD@=1
export @GUARD@
input=$(cat; printf x)
input=${input%x}
output=$(printf '%s' "$input" | /bin/sh -c @ORIGINAL@; printf 'x%s' "$?")
status=${output##*x}
output=${output%x*}
printf '%s' "$output"
case $output in *'
'|'') ;; *) echo ;; esac
line=$(printf '%s' "$input" | @SEGMENT@ 2>/dev/null)
[ -n "$line" ] && printf '%s\n' "$line"
exit "$status"
"""


def _resolve(root: Path, missing: str) -> str:
    versions = root.parent
    if SEMVER.match(root.name) and versions.name == "deckhand" and versions.parent.parent.name == "cache":
        return NEWEST.replace("@VERSIONS@", shlex.quote(str(versions))).replace("@MISSING@", missing)
    return f"bin={shlex.quote(str(root / 'bin' / 'deckhand'))}\n"


def launcher(root: Path) -> str:
    return "#!/bin/sh\n" + _resolve(root, QUIET) + BOUNDED


def command(root: Path) -> str:
    return "#!/bin/sh\n" + _resolve(root, MISSING) + PASS


def wrapper(original: str, segment: str) -> str:
    fills = {"ORIGINAL": shlex.quote(original), "SEGMENT": segment, "GUARD": GUARD}
    return re.sub(r"@(ORIGINAL|SEGMENT|GUARD)@", lambda match: fills[match.group(1)], WRAP)


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
    new = {"id": WIDGET, "type": "custom-command", "commandPath": segment, "timeout": 2000}
    empty = next((row for row in lines if not row), None)
    if empty is not None:
        empty.append(new)
    elif len(lines) < 3:
        lines.append([new])
    else:
        lines[-1] += [{"id": SEPARATOR, "type": "separator"}, new]
    return False, dump(data, raw)


def drop_widget(data: dict[str, Any], raw: bytes) -> bytes | None:
    lines = data.get("lines") if isinstance(data.get("lines"), list) else []
    ids = (WIDGET, SEPARATOR)
    if not any(isinstance(item, dict) and item.get("id") in ids for row in lines for item in row):
        return None
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
