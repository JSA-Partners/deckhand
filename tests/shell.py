from __future__ import annotations

import json
import subprocess
from pathlib import Path


def row(ref: str, state: str, status: str | None = None, at: int = 1795000000000) -> dict:
    found = {"id": f"id-{ref}", "name": f"hand-{ref}", "sessionId": f"sid-{ref}", "cwd": "/w", "startedAt": at}
    return found | {"state": state} | ({"status": status} if status else {})


class Shell:
    def __init__(self, *rows: dict, fleet_fails: bool = False) -> None:
        self.rows = list(rows)
        self.fleet_fails = fleet_fails
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, argv, cwd=None, stdin=None):
        self.calls.append((argv, cwd))
        out, code = "", 0
        if argv[:2] == ["claude", "agents"]:
            out, code = json.dumps(self.rows), 1 if self.fleet_fails else 0
        elif argv[:2] == ["git", "show-ref"]:
            code = 1
        return subprocess.CompletedProcess(argv, code, out, "")

    def launches(self) -> list[tuple[list[str], Path | None]]:
        return [(argv, cwd) for argv, cwd in self.calls if argv[:2] == ["claude", "--bg"]]

    def launched(self) -> list[str]:
        return [argv[argv.index("--name") + 1] for argv, _ in self.launches()]
