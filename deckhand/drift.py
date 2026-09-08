"""The file references a plan names, and the ones that no longer resolve.

A plan is written into a story's body long before anything reads it back, so by the time `start`
prints it the files it names may have moved or shrunk. The plan comes out of the issue body, so it
is text before it is ever a file, and both helpers take text.
"""

from __future__ import annotations

import re
from pathlib import Path

# A backticked path with an extension, optionally followed by `:LINE` or `:LINE-LINE`.
_REFERENCE = re.compile(r"`([A-Za-z0-9_./@+-]+\.[A-Za-z0-9]+(?::[0-9]+(?:-[0-9]+)?)?)`")


def references(text: str) -> list[str]:
    """Every distinct backticked path or `path:line` reference in `text`, sorted."""
    return sorted(set(_REFERENCE.findall(text)))


def drift_text(text: str, root: Path | None = None) -> list[tuple[str, str]]:
    """`(reference, reason)` for each reference in `text` that does not resolve under `root`."""
    root = root or Path.cwd()
    problems: list[tuple[str, str]] = []
    for ref in references(text):
        path, _, rest = ref.partition(":")
        target = root / path
        if not target.exists():
            problems.append((ref, "missing"))
            continue
        if not rest:
            continue
        line = int(rest.split("-", 1)[0])
        total = target.read_bytes().count(b"\n")
        if line > total:
            problems.append((ref, f"line {line} beyond end of file ({total} lines)"))
    return problems
