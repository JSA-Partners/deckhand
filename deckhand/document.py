"""The document step: docs audited for stale paths, duplicate headings, and files behind what they cite.

The write is the editor's, so the step has the one verb; `context` is the report, and the step's
guard keeps a failure to one line inside whichever skill injected it.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from datetime import datetime
from pathlib import Path

from deckhand.step import step

DEFAULT_DIR = "docs/claude"

# A backticked path or file:line reference: needs a dot (an extension) so bare `code` spans are
# skipped. Whether it is really a path is `_references`, which no language's extensions can date.
REFERENCE = re.compile(r"`([\w./-]+\.[\w-]+(?::\d+)?)`")
HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
# A fenced code block, possibly indented (inside a list item) or never closed (runs to the end of the file).
FENCE = re.compile(r"^[ \t]*([`~]{3,})[^\n]*\n.*?(?:^[ \t]*\1[`~]*[ \t]*$\n?|\Z)", re.MULTILINE | re.DOTALL)
BROKEN, DUPLICATE, STALE = "broken", "duplicate", "stale"
ELSEWHERE = "a file in another repository is a link"


def _configure_context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("dir", nargs="?", default=DEFAULT_DIR, help=f"directory to audit (default: {DEFAULT_DIR})")


@step("document", issue_bound=False, configure_context=_configure_context)
def context(args: argparse.Namespace) -> int:
    """List stale path references, headings duplicated across files, and files behind what they cite."""
    return _audit(args.dir)


def findings(dir_arg: str) -> dict[Path, list[tuple[str, str]]]:
    """Every doc under `dir_arg` with its findings as `(kind, text)`; no directory means no docs."""
    root = Path.cwd() / dir_arg
    files = sorted(p for p in root.rglob("*.md") if p.is_file()) if root.is_dir() else []
    found: dict[Path, list[tuple[str, str]]] = {f: [] for f in files}
    headings: dict[str, list[Path]] = {}
    dates: dict[Path, datetime | None] = {}  # one `git log` per path per run

    for f in files:
        text = _text(f)
        for match in HEADING.finditer(text):
            headings.setdefault(match.group(1).strip(), []).append(f)
        for ref, target in _references(f, text):
            if target is None:
                found[f].append((BROKEN, f"broken reference: `{ref}` ({ELSEWHERE})"))
            elif stale := _stale(f, target, ref, dates):
                found[f].append((STALE, stale))

    for heading, locations in headings.items():
        if len(locations) < 2:
            continue
        for f in sorted(set(locations)):
            others = sorted(set(locations) - {f})
            if others:
                names = ", ".join(_name(o) for o in others)
                found[f].append((DUPLICATE, f'duplicate heading "{heading}" also in {names}'))
            else:
                found[f].append((DUPLICATE, f'duplicate heading "{heading}" repeated in {_name(f)}'))
    return found


def _text(doc: Path) -> str:
    """The doc without its fenced code, where a path is an example rather than a reference."""
    return FENCE.sub("", doc.read_text(encoding="utf-8", errors="replace"))


def _name(path: Path) -> str:
    return str(path.relative_to(Path.cwd()))


def _audit(dir_arg: str) -> int:
    if not (Path.cwd() / dir_arg).is_dir():
        print(f"No docs directory at {dir_arg}.")
        return 0
    found = findings(dir_arg)
    if not any(found.values()):
        print("Nothing stale.")
    for f, items in found.items():
        if items:
            print(f"{_name(f)}:")
            for _, text in items:
                print(f"  {text}")
    return 0


def _references(doc: Path, text: str) -> list[tuple[str, Path | None]]:
    """Backticked tokens that name a path, each with the file it resolves to, or None for a broken one.

    A directory separator makes a token a path whether the file is there or not, so `src/gone.rs` is
    reported. A bare name is ambiguous, and `1.2.3`, `github.com`, and `gone.rs` are indistinguishable
    from here, so one counts only when a file of that name is actually there.
    """
    found = []
    for token in REFERENCE.findall(text):
        path_part = token.split(":", 1)[0]
        target = _resolve(doc, path_part)
        if target is not None or "/" in path_part:
            found.append((token, target))
    return found


def _resolve(doc: Path, path_part: str) -> Path | None:
    """The file `path_part` names, tried from the working directory first, then beside `doc`."""
    for base in (Path.cwd(), doc.parent):
        candidate = base / path_part
        if candidate.exists():
            return candidate
    return None


def _stale(doc: Path, target: Path, ref: str, dates: dict[Path, datetime | None]) -> str | None:
    """`doc` is stale when `target` (the file `ref` points at) was committed after `doc` was."""
    doc_date = _last_commit_date(doc, dates)
    target_date = _last_commit_date(target, dates)
    if doc_date is None or target_date is None or target_date <= doc_date:
        return None
    return f"stale reference: `{ref}` changed after this file was last updated"


def _last_commit_date(path: Path, dates: dict[Path, datetime | None]) -> datetime | None:
    if path not in dates:
        dates[path] = _git_commit_date(path)
    return dates[path]


def _git_commit_date(path: Path) -> datetime | None:
    result = subprocess.run(
        ["git", "log", "-1", "--format=%ci", "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    out = result.stdout.strip()
    if not out:
        return None
    return datetime.strptime(out, "%Y-%m-%d %H:%M:%S %z")
