from __future__ import annotations

import json
import subprocess
from pathlib import Path

DOT = "\u00b7"
LAUNCHED = (
    "Starting background service\u2026\nbackgrounded {dot} {short} {dot} {name}\n  claude agents    list sessions\n"
)
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


class World:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.calls: list[list[str]] = []
        self.fail: set[tuple[str, ...]] = set()
        self.silent = False
        self.reads_before_failing: int | None = None

    def __call__(self, argv, cwd=None, stdin=None):
        self.calls.append(argv)
        if any(tuple(argv[: len(prefix)]) == prefix for prefix in self.fail):
            return subprocess.CompletedProcess(argv, 1, "", "error: one\nfatal: two\n")
        out, code = "", 0
        if argv[:2] == ["claude", "agents"]:
            reads, limit = sum(call[:2] == ["claude", "agents"] for call in self.calls), self.reads_before_failing
            out, code = json.dumps(self.rows), int(limit is not None and reads > limit)
        elif argv[:2] == ["claude", "--bg"]:
            out = self._background(argv, cwd)
            out = "" if self.silent else out
        elif argv[:2] == ["claude", "stop"]:
            self._stop(lambda found: found["id"] == argv[2])
        elif argv[:2] == ["claude", "rm"]:
            self.rows = [found for found in self.rows if found["id"] != argv[2]]
        elif argv[:2] == ["git", "show-ref"]:
            code = 1
        return subprocess.CompletedProcess(argv, code, out, "")

    def _new(self, name: str, cwd: Path | None, session: str | None = None) -> dict:
        short = f"sh{len(self.calls):02d}"
        found = {"id": short, "name": name, "sessionId": session or f"sid-{short}", "cwd": str(cwd)}
        self.rows.append(found | {"startedAt": 1795000000000 + len(self.calls), "state": "working", "status": "busy"})
        return found

    def _background(self, argv: list[str], cwd: Path | None) -> str:
        if "--resume" not in argv:
            name = argv[argv.index("--name") + 1]
            return LAUNCHED.format(dot=DOT, short=self._new(name, cwd)["id"], name=name)
        session = argv[argv.index("--resume") + 1]
        original = next(found for found in self.rows if found["sessionId"] == session)
        if "status" in original:
            copy = self._new(original["name"], cwd)["id"]
            return COPIED.format(dot=DOT, short=original["id"], copy=copy)
        self.rows.remove(original)
        fresh = self._new(original["name"], cwd, session)
        return LAUNCHED.format(dot=DOT, short=fresh["id"], name=fresh["name"])

    def _stop(self, chosen) -> None:
        for found in self.rows:
            if chosen(found):
                found["state"] = "stopped"
                found.pop("status", None)

    def stop(self, name: str) -> None:
        self._stop(lambda found: found["name"] == name)

    def launches(self) -> int:
        return sum(call[:2] == ["claude", "--bg"] for call in self.calls)
