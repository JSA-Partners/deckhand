from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from deckhand import git
from deckhand.step import Refusal, register

USAGE = "commit context prints the staged diff, recent history and the convention; git writes the commit"
COMMITLINT_FILES = (
    ".commitlintrc",
    ".commitlintrc.json",
    ".commitlintrc.yml",
    ".commitlintrc.yaml",
    ".commitlintrc.js",
    ".commitlintrc.cjs",
    "commitlint.config.js",
    "commitlint.config.cjs",
    "commitlint.config.mjs",
    "commitlint.config.ts",
)
GENERIC_PREFIX = re.compile(r"^(?:src|internal|pkg|lib|cmd|app)/")
BACKTICKS = re.compile(r"`+")
CONVENTIONAL = re.compile(r"^[0-9a-f]+ [a-z]+(\([^)]*\))?!?: .+$")
SCOPED = re.compile(r"^[0-9a-f]+ [a-z]+\(")
COMMITLINT_RULE = re.compile(r"config-conventional|type-enum[^\]]*\]")
DIFF_LIMIT = 600
STAGE_RULE = "Stage only the files this commit is about; a shared checkout is never staged with -A."


def fence(diff: str) -> str:
    longest = max((len(m.group(0)) for m in BACKTICKS.finditer(diff)), default=0)
    return "`" * max(longest + 1, 3)


def history_summary(lines: list[str]) -> str:
    if not lines:
        return "no commits yet"
    matched = [line for line in lines if CONVENTIONAL.match(line)]
    ratio = f"{len(matched)}/{len(lines)}"
    if not matched:
        return f"does not consistently follow conventional commits ({ratio} match)"
    scoped = [line for line in matched if SCOPED.match(line)]
    if len(scoped) == len(matched):
        return f"follows `type(scope): summary` ({ratio} commits match)"
    if not scoped:
        return f"follows `type: summary` with no scope ({ratio} commits match)"
    return f"follows `type: summary`, scope used inconsistently ({ratio} commits match)"


def _rules(text: str) -> list[str]:
    return [f"  - {m.group(0)}" for m in list(COMMITLINT_RULE.finditer(text))[:3]]


def commitlint(cwd: Path) -> list[str]:
    for name in COMMITLINT_FILES:
        path = cwd / name
        if path.is_file():
            return [f"- commitlint: {name}", *_rules(path.read_text(encoding="utf-8", errors="replace"))]
    package = cwd / "package.json"
    if not package.is_file():
        return []
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict) and "commitlint" in data:
        return ["- commitlint: package.json", *_rules(json.dumps(data["commitlint"]))]
    return []


def grep_context(lines: list[str], keyword: str, before: int, after: int) -> list[str]:
    blocks: list[list[int]] = []
    for i in (i for i, line in enumerate(lines) if keyword in line.lower()):
        start, end = max(0, i - before), min(len(lines), i + after + 1)
        if blocks and start <= blocks[-1][1]:
            blocks[-1][1] = max(blocks[-1][1], end)
        else:
            blocks.append([start, end])
    out: list[str] = []
    for index, (start, end) in enumerate(blocks):
        out += ["--"] if index else []
        out += lines[start:end]
    return out


def claude_md(cwd: Path) -> list[str]:
    path = cwd / "CLAUDE.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    if "commit" not in text.lower():
        return []
    shown = grep_context(text.splitlines(), "commit", 1, 3)[:20]
    return ["- CLAUDE.md mentions commits:", *(f"  > {line}" for line in shown)]


def infer_scope(staged: list[str]) -> str:
    found = set()
    for path in staged:
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        while GENERIC_PREFIX.match(directory):
            directory = GENERIC_PREFIX.sub("", directory)
        if directory:
            found.add(directory.split("/")[0])
    if not found:
        return "(none, root-level files)"
    if len(found) == 1:
        return found.pop()
    return f"(none, files span {' and '.join(sorted(found))})"


def porcelain_paths(status: str) -> list[str]:
    paths = []
    for line in status.splitlines():
        if line:
            path = line[3:]
            paths.append(path.split(" -> ", 1)[1] if " -> " in path else path)
    return [path.strip('"') for path in paths]


def report(cwd: Path, runner: git.Runner = git.run) -> str:
    def out(*args: str) -> str:
        return git.call(["git", *args], cwd, runner)

    staged = [line for line in out("diff", "--cached", "--name-only", git.UNNESTED).splitlines() if line]
    if not staged:
        status = out("status", "--porcelain", git.UNNESTED)
        lines = ["Nothing is staged.", "", "Unstaged or untracked:", *(f"- {s}" for s in status.splitlines() if s)]
        paths = porcelain_paths(status)
        if paths:
            lines += ["", "Stage: git add -- " + " ".join(shlex.quote(p) for p in paths), "", STAGE_RULE]
        return "\n".join(lines) + "\n"
    diff = out("diff", "--cached", git.UNNESTED)
    marks = fence(diff)
    diff_lines = diff.split("\n")
    log = runner(["git", "log", "--oneline", "-15"], cwd, None)
    history = [line for line in log.stdout.splitlines() if line] if log.returncode == 0 else []
    lines = ["## Staged", "", out("diff", "--cached", "--stat", git.UNNESTED), "", "## Diff", "", f"{marks}diff"]
    lines += diff_lines[:DIFF_LIMIT]
    if len(diff_lines) > DIFF_LIMIT:
        lines.append(f"... (truncated at {DIFF_LIMIT} lines; use git diff --cached for the rest)")
    lines += [marks, "", "## Recent history", ""]
    lines += [f"- {line}" for line in history] or ["- (no commits yet)"]
    lines += ["", "## Convention", "", "- conventional commits (required)"]
    lines += [f"- Recent history: {history_summary(history)}", *commitlint(cwd), *claude_md(cwd)]
    lines.append(f"- Inferred scope: {infer_scope(staged)}")
    return "\n".join(lines) + "\n"


@register("commit")
class Commit:
    def __init__(self, runner: git.Runner = git.run, cwd: Path | None = None) -> None:
        self._runner = runner
        self._cwd = cwd

    def context(self, args: list[str]) -> None:
        if args:
            raise Refusal(f"commit context takes no arguments; {USAGE}")
        print(report(self._cwd or git.working_directory(self._runner), self._runner), end="")

    def apply(self, args: list[str]) -> None:
        raise Refusal(f"commit has no apply; {USAGE}")
