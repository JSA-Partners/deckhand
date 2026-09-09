"""The file references a plan names, and the ones that no longer resolve.

A plan is written into a story's body long before anything reads it back, so by the time `start`
prints it the files it names may have moved or shrunk. The plan comes out of the issue body, so it
is text before it is ever a file, and both helpers take text.

Drift is what the model should open before implementing, so only what the plan expects to already
be there counts. A reference is a path whose last segment ends in an extension, which keeps out the
versions and branch names a release plan names in passing, and a file the plan says it will create
is skipped, because the implementation is what writes it.
"""

from __future__ import annotations

import re
from pathlib import Path

# A backticked path, optionally followed by `:LINE` or `:LINE-LINE`. The extension opens with a
# letter, which is what tells `internal/store/collection.go` from the versions and the branch names
# a release plan is full of: `2.0.0` and `chore/release-v1.1.0-v0.4.0` end in digits, not in a type.
_REFERENCE = re.compile(r"`([A-Za-z0-9_./@+-]+\.[A-Za-z][A-Za-z0-9]*(?::[0-9]+(?:-[0-9]+)?)?)`")

# How a Files block names a file the plan will write; the bullet and the case both vary.
_CREATES = ("- create:", "create:")


def references(text: str) -> list[str]:
    """Every distinct backticked path or `path:line` reference in `text`, sorted."""
    return sorted(set(_REFERENCE.findall(text)))


def _created(text: str) -> set[str]:
    """The paths `text` says it will create, without their line numbers; a plan writes those itself."""
    written: set[str] = set()
    for line in text.splitlines():
        if line.strip().lower().startswith(_CREATES):
            written.update(ref.partition(":")[0] for ref in references(line))
    return written


def drift_text(text: str, root: Path | None = None) -> list[tuple[str, str]]:
    """`(reference, reason)` for each reference in `text` that does not resolve under `root`."""
    root = root or Path.cwd()
    writes = _created(text)
    problems: list[tuple[str, str]] = []
    for ref in references(text):
        path, _, rest = ref.partition(":")
        if path in writes:
            continue
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
