from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from deckhand.git import Runner, apply_hunks, call, run
from deckhand.policy import Policy
from deckhand.step import Refusal
from deckhand.summary import ChangeSummary

USERNAME = "deckhand"
REF_PREFIX = "refs/deckhand/review/"
_RANGE = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class Hunk:
    path: str
    header: str
    lines: list[str]
    reason: str
    old_path: str = ""

    @property
    def paths(self) -> set[str]:
        return {self.path, self.old_path or self.path}


def glob_match(pattern: str, path: str) -> bool:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch("".join(out), path) is not None


def _paths(header: list[str]) -> tuple[str, str]:
    def find(*prefixes: str) -> str:
        for prefix in prefixes:
            for line in header:
                if line.startswith(prefix):
                    return line[len(prefix) :].split("\t")[0]
        return ""

    new = find("+++ b/", "rename to ")
    old = find("--- a/", "rename from ")
    if not new or not old:
        both = re.match(r"diff --git a/(.+) b/(.+)$", header[0])
        new = new or (both.group(2) if both else "")
        old = old or (both.group(1) if both else new)
    return new or old, old or new


def parse_hunks(diff: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    for block in re.split(r"^(?=diff --git )", diff, flags=re.MULTILINE):
        if not block.startswith("diff --git "):
            continue
        lines = block.split("\n")
        if lines[-1] == "":
            lines.pop()
        starts = [i for i, line in enumerate(lines) if line.startswith("@@ ")]
        header = lines[: starts[0]] if starts else lines
        path, old_path = _paths(header)
        text = "\n".join(header) + "\n"
        if not starts:
            hunks.append(Hunk(path, text, [], "", old_path))
            continue
        for start, end in zip(starts, [*starts[1:], len(lines)], strict=True):
            hunks.append(Hunk(path, text, lines[start:end], "", old_path))
    return hunks


def select_hunks(
    diff: str,
    policy: Policy,
    doubtful_paths: set[str],
    manifest: dict,
    summary: ChangeSummary | None = None,
) -> list[Hunk]:
    selected = []
    for hunk in parse_hunks(diff):
        reasons = [
            f"sensitive path {p}" for p in policy.sensitive_paths if any(glob_match(p, path) for path in hunk.paths)
        ]
        if hunk.paths & doubtful_paths:
            reasons.append("file of a doubtful finding")
        if hunk.paths & set(manifest["contract_sites"]):
            reasons.append("contract site")
        if hunk.paths & set(manifest["invariant_sites"]):
            reasons.append("invariant site")
        if summary:
            reasons += [f"new here: {n.text}" for n in summary.new_here if n.path in hunk.paths]
        if not reasons:
            continue
        if not hunk.lines and "\nBinary files " in hunk.header:
            raise Refusal(f"{hunk.path} is a binary change that cannot be trimmed into a review commit")
        selected.append(Hunk(hunk.path, hunk.header, hunk.lines, "; ".join(reasons), hunk.old_path))
    return selected


def _patch(hunks: list[Hunk]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for hunk in hunks:
        if hunk.path not in seen:
            seen.add(hunk.path)
            out.append(hunk.header)
        if hunk.lines:
            out.append("\n".join(hunk.lines) + "\n")
    return "".join(out)


def review_commit(worktree: Path, base: str, hunks: list[Hunk], runner: Runner = run) -> str:
    if not hunks:
        raise Refusal("no hunk is selected for review")
    branch = call(["git", "branch", "--show-current"], worktree, runner).strip()
    if not branch:
        raise Refusal("the review commit needs a branch, but the worktree has a detached head")
    return apply_hunks(worktree, base, _patch(hunks), "review: selected hunks", REF_PREFIX + branch, runner)


def _counts(line: str) -> tuple[int, int, int, int]:
    found = _RANGE.match(line)
    if not found:
        raise Refusal(f"not a hunk header: {line!r}")
    old, old_count, new, new_count = found.groups()
    return int(old), int(old_count or 1), int(new), int(new_count or 1)


def _anchor(hunk: Hunk, shift: int) -> tuple[int, str]:
    old_start, old_count, _, _ = _counts(hunk.lines[0])
    old = old_start
    new = old_start + shift + (1 if old_count == 0 else 0)
    first_removed = None
    for line in hunk.lines[1:]:
        if line.startswith("+"):
            return new, "new"
        if line.startswith("-"):
            first_removed = first_removed or old
            old += 1
        elif line.startswith(" "):
            old += 1
            new += 1
    return first_removed or old_start, "old"


def seed_comments(session: str, hunks: list[Hunk], repo: Path | None = None, runner: Runner = run) -> None:
    shifts: dict[str, int] = {}
    for hunk in hunks:
        argv = ["tuicr", "review", "add", "--session", session]
        if repo is not None:
            argv += ["--repo", str(repo)]
        argv += ["--target-file", hunk.path]
        if hunk.lines:
            shift = shifts.get(hunk.path, 0)
            line, side = _anchor(hunk, shift)
            _, old_count, _, new_count = _counts(hunk.lines[0])
            shifts[hunk.path] = shift + new_count - old_count
            argv += ["--line", str(line), "--side", side]
        call([*argv, "--username", USERNAME, hunk.reason], None, runner)


def command(base: str, sha: str) -> list[str]:
    return ["tuicr", "-r", f"{base}..{sha}"]


def session_for(repo: Path, base: str, sha: str, runner: Runner = run) -> dict | None:
    for session in json.loads(call(["tuicr", "review", "list", "--repo", str(repo)], None, runner) or "[]"):
        first, _, last = str(session.get("slug", "")).rpartition("/commits/")[2].partition("..")
        if first and last and base.startswith(first) and sha.startswith(last):
            return session
    return None
