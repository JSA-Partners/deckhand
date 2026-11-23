from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from deckhand import config, hosts
from deckhand.step import Refusal

SEGMENT = "statusline-segment"
LAUNCHER = hosts.LAUNCHER
WRAPPER = "statusline-wrapper"
STATE = "statusline.json"
MARK = hosts.MARK


@dataclass(frozen=True)
class Places:
    settings: Path
    managed: Path
    project: Path
    ccstatusline: Path
    ccline: Path
    config: Path
    root: Path

    @classmethod
    def here(cls) -> Places:
        home = Path.home()
        claude = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser()
        ccline = Path(os.environ.get("CCLINE_HOME") or home / ".claude" / "ccline").expanduser()
        darwin = sys.platform == "darwin"
        return cls(
            settings=claude / "settings.json",
            managed=Path("/Library/Application Support/ClaudeCode" if darwin else "/etc/claude-code"),
            project=Path.cwd(),
            ccstatusline=home / ".config" / "ccstatusline" / "settings.json",
            ccline=ccline / "config.toml",
            config=config.config_dir(),
            root=Path(__file__).resolve().parent.parent,
        )


@dataclass(frozen=True)
class Edit:
    kind: str
    path: Path
    before: bytes | None
    after: bytes


@dataclass(frozen=True)
class Report:
    owner: str
    method: str = ""
    installed: bool = False
    changes: tuple[Path, ...] = ()
    notes: tuple[str, ...] = ()
    blocked: str = ""


@dataclass
class _Plan:
    owner: str
    method: str = ""
    installed: bool = False
    edits: list[Edit] = field(default_factory=list)
    original: dict | None = None
    line: dict | None = None
    notes: list[str] = field(default_factory=list)


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _json(path: Path, raw: bytes | None) -> dict[str, Any]:
    if raw is None or not raw.strip():
        return {}
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError as error:
        raise Refusal(f"{path} is not valid JSON: {error}") from None
    if not isinstance(data, dict):
        raise Refusal(f"{path} must hold a JSON object")
    return data


def _segment(places: Places, color: bool = False) -> str:
    return f"{MARK} {shlex.quote(str(places.config / SEGMENT))}" + (" --color" if color else "")


def _mentions(text: str, places: Places) -> bool:
    paths = [str(places.config / name) for name in (SEGMENT, WRAPPER)]
    return any(path in text or shlex.quote(path) in text for path in paths)


def _state(places: Places) -> dict[str, Any]:
    try:
        state = json.loads((places.config / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _ours(text: str) -> bool:
    return MARK in text


def _set_line(plan: _Plan, places: Places, data: dict, raw: bytes | None, line: dict) -> None:
    plan.line = line
    updated = {**data, "statusLine": line}
    plan.edits.append(Edit("settings", places.settings, raw, hosts.dump(updated, raw)))


def _plan(places: Places, data: dict, raw: bytes | None) -> _Plan:
    current = data.get("statusLine")
    line = current if isinstance(current, dict) else {}
    command = line.get("command") if isinstance(line.get("command"), str) else ""
    plan = _Plan(hosts.owner(command), original=current if isinstance(current, dict) else None)
    if command and _ours(command):
        plan.installed = True
        owner = _state(places).get("owner") or ("claude-hud" if "claude-hud" in command else "none")
        plan.owner = "deckhand" if owner == "none" else owner
        return plan
    if _mentions(command, places):
        raise Refusal(
            f"{places.settings}: statusLine already runs deckhand's {SEGMENT} or {WRAPPER}; restore it by hand"
        )
    if plan.owner == "none":
        plan.method = "statusLine"
        new = {**line, "type": "command", "command": _segment(places, color=True), "refreshInterval": 5}
        _set_line(plan, places, data, raw, new)
        return plan
    if plan.owner == "claude-hud":
        edited = hosts.hud(command, _segment(places))
        if edited is not None:
            plan.method = "extra-cmd"
            plan.notes.append(
                "claude-hud shows it on its first line, without color and cut at 50 characters;"
                " /hud install rewrites the command, so run setup again after one"
            )
            _set_line(plan, places, data, raw, {**line, "command": edited})
            return plan
        plan.notes.append("claude-hud's command has a shape setup does not edit, so it is wrapped instead")
    tools = {
        "ccstatusline": ("widget", hosts.ccstatusline_file(command, places.ccstatusline), hosts.widget, "ccstatusline"),
        "ccometixline": ("cmd item", places.ccline, hosts.cmd_item, "ccline"),
    }
    if plan.owner in tools:
        method, path, fit, kind = tools[plan.owner]
        raw_tool = _read(path) if path is not None else None
        installed, after = fit(raw_tool, _segment(places), _ours)
        if installed or after is not None:
            plan.method, plan.installed = method, installed
            if after is not None:
                plan.edits.append(Edit(kind, path, raw_tool, after))
            return plan
        plan.notes.append(f"{path or 'its config'} has no lines setup can add to, so {plan.owner} is wrapped instead")
    plan.method = "wrapper"
    _set_line(plan, places, data, raw, {**line, "command": f"{MARK} {shlex.quote(str(places.config / WRAPPER))}"})
    return plan


def _managed(places: Places) -> list[Path]:
    return [places.managed / "managed-settings.json", *sorted((places.managed / "managed-settings.d").glob("*.json"))]


def _blocked(places: Places, data: dict) -> str:
    for path in _managed(places):
        try:
            managed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(managed, dict):
            continue
        if "statusLine" in managed:
            return f"managed settings in {path} set the status line"
        if managed.get("allowManagedHooksOnly") is True:
            return f"managed settings in {path} allow only a managed status line"
        if managed.get("disableAllHooks") is True:
            return f"disableAllHooks in {path} turns the status line off"
    if data.get("disableAllHooks") is True:
        return f"disableAllHooks in {places.settings} turns the status line off"
    return ""


def _project_notes(places: Places) -> list[str]:
    notes = []
    for name in ("settings.local.json", "settings.json"):
        path = places.project / ".claude" / name
        if path.resolve() == places.settings.resolve():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "statusLine" in data:
            notes.append(f"{path} sets this project's own status line, which wins in this project")
    return notes


def _inspect(places: Places) -> tuple[dict, bytes | None, _Plan]:
    raw = _read(places.settings)
    data = _json(places.settings, raw)
    blocked = _blocked(places, data)
    if blocked:
        raise Refusal(f"setup leaves the status line alone: {blocked}")
    return data, raw, _plan(places, data, raw)


def report(places: Places) -> Report:
    try:
        _, _, plan = _inspect(places)
    except Refusal as refusal:
        raw = _read(places.settings)
        try:
            owner = _plan(places, _json(places.settings, raw), raw).owner
        except Refusal:
            owner = "unknown"
        return Report(owner=owner, blocked=refusal.rule)
    changes = () if plan.installed else tuple(edit.path for edit in plan.edits)
    notes = tuple(plan.notes + _project_notes(places))
    return Report(plan.owner, plan.method, plan.installed, changes, notes)


def _write(path: Path, data: bytes, mode: int = 0o644) -> None:
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = target.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    handle, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.chmod(temp, mode)
        os.replace(temp, target)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _backup(path: Path, now: datetime) -> None:
    target = path.resolve()
    if not target.exists():
        return
    stem = f"{path.name}.deckhand-{now:%Y%m%d-%H%M%S}"
    backup = path.with_name(stem)
    count = 1
    while backup.exists():
        count += 1
        backup = path.with_name(f"{stem}-{count}")
    shutil.copy2(target, backup)


def _own(path: Path, text: str, mode: int) -> None:
    if _read(path) != text.encode():
        _write(path, text.encode(), mode)


def launch(places: Places) -> list[str]:
    path, text = places.config / LAUNCHER, hosts.command(places.root)
    if _read(path) == text.encode():
        return []
    places.config.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write(path, text.encode(), 0o700)
    return [f"wrote {path}"]


def install(places: Places, now: datetime | None = None) -> list[str]:
    now = now or datetime.now()
    _, _, plan = _inspect(places)
    places.config.mkdir(mode=0o700, parents=True, exist_ok=True)
    _own(places.config / SEGMENT, hosts.launcher(places.root), 0o700)
    launch(places)
    if plan.installed:
        return [f"status line: the deckhand segment is already installed ({plan.owner})"]
    if plan.method == "wrapper":
        original = (plan.original or {}).get("command", "")
        _own(places.config / WRAPPER, hosts.wrapper(original, _segment(places, color=True)), 0o700)
    written = {str(edit.path) for edit in plan.edits}
    earlier = [
        entry
        for entry in _state(places).get("edits", [])
        if isinstance(entry, dict)
        and entry.get("kind") in ("ccstatusline", "ccline")
        and entry.get("path") not in written
        and _ours((_read(Path(str(entry.get("path")))) or b"").decode("utf-8", "replace"))
    ]
    state = {
        "owner": plan.owner,
        "method": plan.method,
        "original": plan.original,
        "line": plan.line,
        "edits": [
            {
                "kind": e.kind,
                "path": str(e.path),
                "before": None if e.before is None else e.before.decode(),
                "after": e.after.decode(),
            }
            for e in plan.edits
        ]
        + earlier,
    }
    _write(places.config / STATE, (json.dumps(state, indent=2) + "\n").encode(), 0o600)
    for edit in plan.edits:
        _backup(edit.path, now)
        _write(edit.path, edit.after)
    return [f"status line: {plan.method} for {plan.owner}, wrote {edit.path}" for edit in plan.edits]


def _reverse(entry: dict, current: bytes, state: dict, places: Places) -> bytes | None:
    path = Path(entry["path"])
    if entry["kind"] == "settings":
        data = _json(path, current)
        if data.get("statusLine") != state.get("line"):
            return None
        if state.get("original") is None:
            data.pop("statusLine")
        else:
            data["statusLine"] = state["original"]
        return hosts.dump(data, current)
    if entry["kind"] == "ccstatusline":
        return hosts.drop_widget(_json(path, current), current)
    text = current.decode()
    block = hosts.ccline_block(_segment(places))
    return text.replace(block, "", 1).encode() if block in text else None


def _loaded(path: Path, raw: bytes) -> dict[str, Any]:
    try:
        state = json.loads(raw)
    except ValueError:
        state = None
    edits = state.get("edits") if isinstance(state, dict) else None
    valid = isinstance(edits, list) and all(
        isinstance(e, dict)
        and e.get("kind") in ("settings", "ccstatusline", "ccline")
        and isinstance(e.get("path"), str)
        and isinstance(e.get("after"), str)
        and (e.get("before") is None or isinstance(e.get("before"), str))
        for e in edits
    )
    if not valid or not isinstance(state, dict):
        raise Refusal(f"{path} is not what setup wrote; restore from the .deckhand- backups by hand")
    return state


def remove(places: Places, now: datetime | None = None) -> list[str]:
    now = now or datetime.now()
    state_path = places.config / STATE
    raw = _read(state_path)
    if raw is None:
        return ["status line: nothing to remove"]
    state = _loaded(state_path, raw)
    restores: list[tuple[Path, bytes | None]] = []
    for entry in state["edits"]:
        path = Path(entry["path"])
        current = _read(path)
        if current is None:
            continue
        if current == entry["after"].encode():
            before = entry["before"]
            restores.append((path, None if before is None else before.encode()))
            continue
        reversed_ = _reverse(entry, current, state, places)
        if reversed_ is not None:
            restores.append((path, reversed_))
        elif _ours(current.decode("utf-8", "replace")):
            raise Refusal(f"{path} changed since setup and still runs the deckhand segment; edit it by hand")
    lines = []
    for path, content in restores:
        _backup(path, now)
        if content is None:
            path.unlink()
        else:
            _write(path, content)
        lines.append(f"status line: restored {path}")
    for name in (SEGMENT, WRAPPER, STATE):
        (places.config / name).unlink(missing_ok=True)
    return lines or ["status line: nothing of deckhand's was left to restore"]
