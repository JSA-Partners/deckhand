from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from deckhand import git
from deckhand.step import Refusal, register

USAGE = "document context [DIR] audits the docs, docs/claude by default; the editor writes them"
DEFAULT_DIR = "docs/claude"
BASE = "origin/HEAD"
# A backticked token with an extension; whether it names a path is decided by _references.
REFERENCE = re.compile(r"`([\w./-]+\.[\w-]+(?::\d+)?)`")
HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
FENCE = re.compile(r"^[ \t]*([`~]{3,})[^\n]*\n.*?(?:^[ \t]*\1[`~]*[ \t]*$\n?|\Z)", re.MULTILINE | re.DOTALL)
ELSEWHERE = "a file in another repository is a link"
BRANCH_HEADING = "Docs naming a file this branch changed:"


class Audit:
    def __init__(self, root: Path, runner: git.Runner) -> None:
        self.root = root
        self.runner = runner
        self.dates: dict[Path, datetime | None] = {}

    def text(self, doc: Path) -> str:
        return FENCE.sub("", doc.read_text(encoding="utf-8", errors="replace"))

    def name(self, path: Path) -> str:
        return str(path.relative_to(self.root))

    def findings(self, directory: Path) -> dict[Path, list[str]]:
        files = sorted(p for p in directory.rglob("*.md") if p.is_file())
        found: dict[Path, list[str]] = {f: [] for f in files}
        headings: dict[str, list[Path]] = {}
        for f in files:
            text = self.text(f)
            for match in HEADING.finditer(text):
                headings.setdefault(match.group(1).strip(), []).append(f)
            for ref, target in self.references(f, text):
                if target is None:
                    found[f].append(f"broken reference: `{ref}` ({ELSEWHERE})")
                elif self.later(target, f):
                    found[f].append(f"stale reference: `{ref}` changed after this file was last updated")
        for heading, places in headings.items():
            if len(places) < 2:
                continue
            for f in sorted(set(places)):
                others = sorted(set(places) - {f})
                names = ", ".join(self.name(o) for o in others)
                where = f"also in {names}" if others else f"repeated in {self.name(f)}"
                found[f].append(f'duplicate heading "{heading}" {where}')
        return found

    def references(self, doc: Path, text: str) -> list[tuple[str, Path | None]]:
        found = []
        for match in REFERENCE.finditer(text):
            if text[max(match.start() - 1, 0) : match.start()] == "[" and text[match.end() : match.end() + 2] == "](":
                continue
            token = match.group(1)
            part = token.split(":", 1)[0]
            target = next((b / part for b in (self.root, doc.parent) if (b / part).exists()), None)
            if target is None and ("/" not in part or self.ignored(part)):
                continue
            found.append((token, target))
        return found

    def ignored(self, part: str) -> bool:
        return self.runner(["git", "check-ignore", "-q", "--no-index", part], self.root, None).returncode == 0

    def date(self, path: Path) -> datetime | None:
        if path not in self.dates:
            done = self.runner(["git", "log", "-1", "--format=%ci", "--", str(path)], self.root, None)
            out = done.stdout.strip() if done.returncode == 0 else ""
            self.dates[path] = datetime.strptime(out, "%Y-%m-%d %H:%M:%S %z") if out else None
        return self.dates[path]

    def later(self, target: Path, doc: Path) -> bool:
        doc_date, target_date = self.date(doc), self.date(target)
        return doc_date is not None and target_date is not None and target_date > doc_date

    def changed(self) -> set[Path]:
        try:
            base = git.merge_base(self.root, "HEAD", BASE, self.runner)
            names = git.call(["git", "diff", "--name-only", "--relative", f"{base}..HEAD"], self.root, self.runner)
        except git.GitError:
            return set()
        return {(self.root / name).resolve() for name in names.splitlines() if name}

    def branch_lines(self, docs: list[Path]) -> list[str]:
        changed = self.changed()
        lines = []
        for doc in docs if changed else []:
            refs = [r for r, t in self.references(doc, self.text(doc)) if t and t.resolve() in changed]
            if refs:
                lines.append(f"  {self.name(doc)}: {', '.join(f'`{r}`' for r in refs)}")
        return lines


def report(root: Path, directory: str, runner: git.Runner = git.run) -> str:
    where = root / directory
    if not where.is_dir():
        return f"No docs directory at {directory}.\n"
    audit = Audit(root, runner)
    found = audit.findings(where)
    lines = [] if any(found.values()) else ["Nothing stale."]
    for f, items in found.items():
        lines += [f"{audit.name(f)}:", *(f"  {item}" for item in items)] if items else []
    branch = audit.branch_lines(list(found))
    lines += ["", BRANCH_HEADING, *branch] if branch else []
    return "\n".join(lines) + "\n"


@register("document")
class Document:
    def __init__(self, runner: git.Runner = git.run, cwd: Path | None = None) -> None:
        self._runner = runner
        self._cwd = cwd

    def context(self, args: list[str]) -> None:
        if len(args) > 1:
            raise Refusal(f"document context takes at most one directory; {USAGE}")
        print(
            report(self._cwd or git.working_directory(self._runner), args[0] if args else DEFAULT_DIR, self._runner),
            end="",
        )

    def apply(self, args: list[str]) -> None:
        raise Refusal(f"document has no apply; {USAGE}")
