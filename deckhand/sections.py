"""Read and write the `###` sections of a story body.

Only the story's own section names start a section. Any other heading, such as the
`### Task N` blocks inside Plan, belongs to the section above it.
"""

from __future__ import annotations

import re
from typing import Any

SECTIONS = ["Story", "Scope", "Acceptance Criteria", "Plan", "Notes"]
HEADING = re.compile(r"^### (" + "|".join(re.escape(s) for s in SECTIONS) + r")\s*$")

# `get` has to tell "no default" from a default of None, and None is a default a caller wants.
_RAISE = object()


class MissingSection(KeyError):
    """A section name not present in the body; str() is the message, not a repr."""

    def __str__(self) -> str:
        return self.args[0]


def parse(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (preamble, [(name, body)]). Bodies exclude the heading and outer blank lines."""
    preamble, parsed, current, buf = [], [], None, []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line in lines:
        match = HEADING.match(line)
        if match:
            if current is None:
                preamble = buf
            else:
                parsed.append((current, "\n".join(buf).strip("\n")))
            current, buf = match.group(1), []
        else:
            buf.append(line)
    if current is None:
        preamble = buf
    else:
        parsed.append((current, "\n".join(buf).strip("\n")))
    return "\n".join(preamble).strip("\n"), parsed


def render(preamble: str, parsed: list[tuple[str, str]]) -> str:
    parts = [preamble] if preamble else []
    for name, body in parsed:
        parts.append(f"### {name}\n\n{body}".rstrip("\n"))
    return "\n\n".join(parts) + "\n"


def get(text: str, name: str, default: Any = _RAISE) -> Any:
    """The body of the section named `name`; `default`, when one is given, answers a missing section.

    A caller that has something sensible to do without the section says so with `default`; every
    other caller gets `MissingSection`, so a typo in a section name is never a silent empty string.
    """
    for section, body in parse(text)[1]:
        if section == name:
            return body
    if default is not _RAISE:
        return default
    raise MissingSection(f"no section named {name}")


def replace(text: str, name: str, content: str) -> str:
    """`text` with the section named `name` holding `content`, added in section order when absent."""
    if name not in SECTIONS:
        raise ValueError(f"unknown section {name} (one of: {', '.join(SECTIONS)})")
    preamble, parsed = parse(text)
    content = content.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    bad = [line for line in content.split("\n") if HEADING.match(line)]
    if bad:
        raise ValueError(f"content contains a section heading: {bad[0]!r}")
    names = [section for section, _ in parsed]
    if name in names:
        i = names.index(name)
        parsed[i] = (name, content)
    else:
        parsed.append((name, content))
        order = {s: i for i, s in enumerate(SECTIONS)}
        parsed.sort(key=lambda kv: order[kv[0]])
    return render(preamble, parsed)
