from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from deckhand import git, versions
from deckhand.records import Record
from deckhand.step import Refusal, register
from deckhand.steps.build import StoryStep, branch_of, parse
from deckhand.store import Store

USAGE = "checks takes REF and --team-key KEY on both verbs; context [--json], apply runs the commands"
KIND = "checks"
TAIL_LINES = 20
TIMEOUT = 600

Checker = Callable[[str, Path], subprocess.CompletedProcess[str]]


def run_command(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, -1, "", f"timed out after {TIMEOUT} seconds")


def passing(records: list[Record], story: str, head: str) -> bool:
    held = versions.held(records, KIND, story)
    return held is not None and held.version == head and bool(held.data.get("passed"))


class Checks(StoryStep):
    def __init__(
        self,
        store: Store | None = None,
        runner: git.Runner = git.run,
        repo: Path | None = None,
        checker: Checker = run_command,
    ) -> None:
        super().__init__(store, runner, repo)
        self._checker = checker

    def _worktree(self, args: list[str]) -> tuple[dict, list[Record], str, list[str], Path]:
        parsed = parse(args, USAGE)
        if len(parsed["words"]) != 1:
            raise Refusal(f"checks takes one story REF; {USAGE}")
        clone = self._clone()
        feature, story, policy = self._story(parsed["words"][0], parsed["team_key"], clone)
        records = feature.records.get(story["id"], [])
        branch = branch_of(records, story)
        path = git.worktree_path(clone, branch) if branch else None
        if path is None or not path.is_dir():
            raise Refusal(f"{story['identifier']} has no worktree; run build apply {story['identifier']} worktree")
        return story, records, git.head(path, self._runner), policy.checks, path

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
        failed, tail = None, ""
        for command in commands:
            done = self._checker(command, path)
            if done.returncode != 0:
                failed, tail = command, git.stderr_tail(f"{done.stdout}\n{done.stderr}", TAIL_LINES)
                break
        data = {"head": head, "passed": failed is None, "command": failed, "tail": tail if failed else None}
        self._store_for_run().put_record(story["id"], Record(KIND, story["id"], head, "agent:checks", data))
        if failed:
            # The failure is recorded first so the record exists, then refused so the hand sees it.
            raise Refusal(f"{failed} failed at {head}; fix it and run checks again:\n{tail}")
        print(f"done: {len(commands) or 'no'} checks passed for {story['identifier']} at {head}")


register("checks")(Checks)
