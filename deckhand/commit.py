"""The commit step: the staged diff, recent history, the enforced convention, and the scope.

Uses only `git` and the filesystem (no gh). The write is git's own, so the step has the one verb:
`context` is injected verbatim into the /deckhand:commit skill, and the step's guard turns any
failure into one line while the command still exits 0.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from deckhand import git
from deckhand.step import step

COMMITLINT_FILES = [
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
]
GENERIC_PREFIX = re.compile(r"^(?:src|internal|pkg|lib|cmd|app)/")
_BACKTICK_RUN = re.compile(r"`+")
_CONVENTIONAL_SUBJECT = re.compile(r"^[0-9a-f]+ [a-z]+(\([^)]*\))?!?: .+$")
_SCOPED_SUBJECT = re.compile(r"^[0-9a-f]+ [a-z]+\(")
_COMMITLINT_RULE = re.compile(r"config-conventional|type-enum[^\]]*\]")
DIFF_LIMIT = 600


def _fence(diff: str) -> str:
    """One more backtick than the longest run already in the diff, never fewer than 3."""
    longest = max((len(m.group(0)) for m in _BACKTICK_RUN.finditer(diff)), default=0)
    return "`" * max(longest + 1, 3)


def _history_lines(cwd: Path) -> list[str]:
    try:
        text = git.run("log", "--oneline", "-15", cwd=cwd)
    except git.GitError:
        return []
    return [line for line in text.splitlines() if line]


def _history_summary(lines: list[str]) -> str:
    """One line saying what the recent commit subjects establish about the convention.

    Any `word: text` subject counts as conventional; the type list itself is not validated.
    """
    if not lines:
        return "no commits yet"
    matched = [line for line in lines if _CONVENTIONAL_SUBJECT.match(line)]
    ratio = f"{len(matched)}/{len(lines)}"
    if not matched:
        return f"does not consistently follow conventional commits ({ratio} match)"
    scoped = [line for line in matched if _SCOPED_SUBJECT.match(line)]
    if len(scoped) == len(matched):
        return f"follows `type(scope): summary` ({ratio} commits match)"
    if not scoped:
        return f"follows `type: summary` with no scope ({ratio} commits match)"
    return f"follows `type: summary`, scope used inconsistently ({ratio} commits match)"


def _extract_rules(text: str) -> list[str]:
    return [f"  - {m.group(0)}" for m in list(_COMMITLINT_RULE.finditer(text))[:3]]


def _commitlint(cwd: Path) -> list[str]:
    for name in COMMITLINT_FILES:
        path = cwd / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            return [f"- commitlint: {name}"] + _extract_rules(text)
    package_json = cwd / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and "commitlint" in data:
            return ["- commitlint: package.json"] + _extract_rules(json.dumps(data["commitlint"]))
    return []


def _grep_context(lines: list[str], keyword: str, before: int, after: int) -> list[str]:
    """`grep -i -B<before> -A<after> keyword`: matched blocks, merged when overlapping, `--` between gaps."""
    matches = [i for i, line in enumerate(lines) if keyword in line.lower()]
    if not matches:
        return []
    blocks: list[list[int]] = []
    for i in matches:
        start, end = max(0, i - before), min(len(lines), i + after + 1)
        if blocks and start <= blocks[-1][1]:
            blocks[-1][1] = max(blocks[-1][1], end)
        else:
            blocks.append([start, end])
    out: list[str] = []
    for index, (start, end) in enumerate(blocks):
        if index:
            out.append("--")
        out.extend(lines[start:end])
    return out


def _claude_md(cwd: Path) -> list[str]:
    path = cwd / "CLAUDE.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    if "commit" not in text.lower():
        return []
    context = _grep_context(text.splitlines(), "commit", 1, 3)[:20]
    return ["- CLAUDE.md mentions commits:"] + [f"  > {line}" for line in context]


def _infer_scope(staged: list[str]) -> str:
    entries = []
    for path in staged:
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        if not directory:
            entries.append("")
            continue
        while GENERIC_PREFIX.match(directory):  # src/lib/api becomes api
            directory = GENERIC_PREFIX.sub("", directory)
        entries.append(directory.split("/")[0])
    non_empty = sorted({e for e in entries if e})
    if not non_empty:
        return "(none, root-level files)"
    if len(non_empty) == 1:
        return non_empty[0]
    return f"(none, files span {' and '.join(non_empty)})"


def commit_context(cwd: Path) -> str:
    """The full commit context report for the repository at `cwd`."""
    staged = [line for line in git.run("diff", "--cached", "--name-only", cwd=cwd).splitlines() if line]
    if not staged:
        status = git.run("status", "--porcelain", cwd=cwd)
        lines = ["Nothing is staged.", "", "Unstaged or untracked:"]
        lines += [f"- {line}" for line in status.splitlines() if line]
        return "\n".join(lines) + "\n"

    stat = git.run("diff", "--cached", "--stat", cwd=cwd)
    diff = git.run("diff", "--cached", cwd=cwd)
    fence = _fence(diff)
    diff_lines = diff.split("\n")  # not splitlines(): a lone \r is diff content
    truncated = len(diff_lines) > DIFF_LIMIT

    history_lines = _history_lines(cwd)

    out = ["## Staged", "", stat, "", "## Diff", "", f"{fence}diff"]
    out += diff_lines[:DIFF_LIMIT]
    if truncated:
        out.append(f"... (truncated at {DIFF_LIMIT} lines; use git diff --cached for the rest)")
    out += [fence, "", "## Recent history", ""]
    out += [f"- {line}" for line in history_lines] if history_lines else ["- (no commits yet)"]
    out += [
        "",
        "## Convention",
        "",
        "- conventional commits (required)",
        f"- Recent history: {_history_summary(history_lines)}",
    ]
    out += _commitlint(cwd)
    out += _claude_md(cwd)
    out.append(f"- Inferred scope: {_infer_scope(staged)}")
    return "\n".join(out) + "\n"


@step("commit", issue_bound=False)
def context(args: argparse.Namespace) -> int:
    """Print the staged diff, recent history, and commit convention for the current repository."""
    print(commit_context(Path.cwd()), end="")
    return 0
