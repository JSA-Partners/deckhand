from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from deckhand import claims, git, versions
from deckhand.checks import KIND, passing
from deckhand.records import Record
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import StoryStep, parse
from deckhand.store import Store

USAGE = "checks takes REF and [--team-key KEY] on both verbs; context [--json], apply runs the commands"
TAIL_LINES = 20
TIMEOUT = 600

Checker = Callable[[str, Path], subprocess.CompletedProcess[str]]


def run_command(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, -1, "", f"timed out after {TIMEOUT} seconds")


class Checks(StoryStep):
    def __init__(
        self,
        store: Store | None = None,
        runner: git.Runner = git.run,
        repo: Path | None = None,
        checker: Checker = run_command,
        *,
        me: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(store, runner, repo, me=me)
        self._checker = checker

    def _worktree(self, args: list[str]) -> tuple[dict, list[Record], str, list[str], Path]:
        parsed = parse(args, USAGE)
        if len(parsed["words"]) != 1:
            raise Refusal(f"checks takes one story REF; {USAGE}")
        at = self.locate(parsed["words"][0], team_key(parsed))
        if at.path is None or at.head is None:
            ref = at.story["identifier"]
            raise Refusal(f"{ref} has no worktree; run build apply {ref} worktree")
        return at.story, at.records, at.head, at.policy.checks, at.path

    def context(self, args: list[str]) -> None:
        story, records, head, commands, path = self._worktree(args)
        held = versions.held(records, KIND, story["id"])
        facts = {
            "story": story["identifier"],
            "worktree": str(path),
            "head": head,
            "commands": commands,
            "passed_at_head": passing(records, story["id"], head),
            "last": held.data if held else None,
        }
        if "--json" in args:
            print(json.dumps(facts, indent=2))
            return
        print(f"checks for {facts['story']} in {facts['worktree']} at {head}")
        if not commands:
            print("no checks are configured; apply records a pass")
        for command in commands:
            print(f"- {command}")
        print("passed at this head" if facts["passed_at_head"] else "no passing record for this head")

    def apply(self, args: list[str]) -> None:
        story, _, head, commands, path = self._worktree(args)
        claims.owned(story, self.viewer())
        if git.dirty(path, self._runner):
            raise Refusal(f"{path} has uncommitted changes; commit them first so the pass is pinned to {head}")
        failed, tail = None, ""
        for command in commands:
            done = self._checker(command, path)
            if done.returncode != 0:
                failed, tail = command, git.stderr_tail(f"{done.stdout}\n{done.stderr}", TAIL_LINES)
                break
        data = {"head": head, "passed": failed is None, "command": failed, "tail": tail if failed else None}
        self._store_for_run().put_record(story["id"], Record(KIND, story["id"], head, "agent:checks", data))
        if failed:
            raise Refusal(f"{failed} failed at {head}; the failure is recorded. Fix it and run checks again:\n{tail}")
        print(f"done: {len(commands) or 'no'} checks passed for {story['identifier']} at {head}")


register("checks")(Checks)
