from __future__ import annotations

import json
import subprocess
from pathlib import Path

DOT = "·"
LAUNCHED = "Starting background service…\nbackgrounded {dot} {short} {dot} {name}\n  claude agents    list sessions\n"
COPIED = (
    "note: session {short} is already running in the background, so this started a copy as {copy}."
    " `claude attach {short}` opens the original.\nbackgrounded {dot} {copy}\n  claude agents    list sessions\n"
)


def row(ref: str, state: str, status: str | None = None, at: int = 1795000000000) -> dict:
    found = {"id": f"id-{ref}", "name": f"hand-{ref}", "sessionId": f"sid-{ref}", "cwd": "/w", "startedAt": at}
    return found | {"state": state} | ({"status": status} if status else {})


class Shell:
    def __init__(
        self,
        *rows: dict,
        fleet_fails: bool = False,
        short: str = "1444880d",
        copy: str | None = None,
        fail: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        self.rows = list(rows)
        self.fleet_fails = fleet_fails
        self.short = short
        self.copy = copy
        self.fail = fail
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, argv, cwd=None, stdin=None):
        self.calls.append((argv, cwd))
        out, err, code = "", "", 0
        if any(tuple(argv[: len(prefix)]) == prefix for prefix in self.fail):
            err, code = "fatal: unable to access origin\n", 1
        elif argv[:2] == ["claude", "agents"]:
            out, code = json.dumps(self.rows), 1 if self.fleet_fails else 0
        elif argv[:2] == ["claude", "--bg"]:
            out = self._backgrounded(argv)
        elif argv[:2] == ["git", "show-ref"]:
            code = 1
        return subprocess.CompletedProcess(argv, code, out, err)

    def _backgrounded(self, argv: list[str]) -> str:
        if self.copy is not None:
            return COPIED.format(dot=DOT, short=self.short, copy=self.copy)
        name = argv[argv.index("--name") + 1] if "--name" in argv else "probe-c"
        return LAUNCHED.format(dot=DOT, short=self.short, name=name)

    def launches(self) -> list[tuple[list[str], Path | None]]:
        return [(argv, cwd) for argv, cwd in self.calls if argv[:2] == ["claude", "--bg"]]

    def launched(self) -> list[str]:
        return [argv[argv.index("--name") + 1] for argv, _ in self.launches() if "--name" in argv]
