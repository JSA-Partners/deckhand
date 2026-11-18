from __future__ import annotations

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


def _file_path(header: list[str]) -> str:
    for prefix in ("+++ b/", "--- a/"):
        for line in header:
            if line.startswith(prefix):
                return line[len(prefix) :].split("\t")[0]
    return ""


def parse_hunks(diff: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    for block in re.split(r"^(?=diff --git )", diff, flags=re.MULTILINE):
        lines = block.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        starts = [i for i, line in enumerate(lines) if line.startswith("@@ ")]
        if not starts:
            continue
        header = lines[: starts[0]]
        path = _file_path(header)
        text = "\n".join(header) + "\n"
        for start, end in zip(starts, [*starts[1:], len(lines)], strict=True):
            hunks.append(Hunk(path, text, lines[start:end], ""))
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
        reasons = [f"sensitive path {p}" for p in policy.sensitive_paths if glob_match(p, hunk.path)]
        if hunk.path in doubtful_paths:
            reasons.append("file of a doubtful finding")
        if hunk.path in manifest["contract_sites"]:
            reasons.append("contract site")
        if hunk.path in manifest["invariant_sites"]:
            reasons.append("invariant site")
        if summary:
            reasons += [f"new here: {n.text}" for n in summary.new_here if n.path == hunk.path]
        if reasons:
            selected.append(Hunk(hunk.path, hunk.header, hunk.lines, "; ".join(reasons)))
    return selected


def _patch(hunks: list[Hunk]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for hunk in hunks:
        if hunk.path not in seen:
            seen.add(hunk.path)
            out.append(hunk.header)
        out.append("\n".join(hunk.lines) + "\n")
    return "".join(out)


def review_commit(worktree: Path, base: str, hunks: list[Hunk], runner: Runner = run) -> str:
    if not hunks:
        raise Refusal("no hunk is selected for review")
    branch = call(["git", "branch", "--show-current"], worktree, runner).strip()
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
        shift = shifts.get(hunk.path, 0)
        line, side = _anchor(hunk, shift)
        _, old_count, _, new_count = _counts(hunk.lines[0])
        shifts[hunk.path] = shift + new_count - old_count
        argv = ["tuicr", "review", "add", "--session", session]
        if repo is not None:
            argv += ["--repo", str(repo)]
        argv += ["--target-file", hunk.path, "--line", str(line), "--side", side]
        argv += ["--username", USERNAME, hunk.reason]
        call(argv, None, runner)


def command(base: str, sha: str) -> list[str]:
    return ["tuicr", "-r", f"{base}..{sha}"]
