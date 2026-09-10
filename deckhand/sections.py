"""Read and write the `###` sections of a story body.

Only the story's own section names start a section. Any other heading, such as the
`### Task N` blocks inside Plan, belongs to the section above it.

The collapsed block round the Plan belongs to these commands rather than to the story: `render`
puts it on, `parse` takes it off, so no reader and no draft ever carries it.
"""

from __future__ import annotations

import re
from typing import Any

SECTIONS = ["Story", "Scope", "Acceptance Criteria", "Plan", "Notes"]
HEADING = re.compile(r"^### (" + "|".join(re.escape(s) for s in SECTIONS) + r")\s*$")

FOLD_OPEN = "<details>\n<summary>Show the plan</summary>"
FOLD_CLOSE = "</details>"
# The fold as it comes back, which is rarely the fold that went out: GitHub's editor, a model
# rewriting the draft, or a hand edit all reshape it, and any of those still has to be recognised.
_FOLD = re.compile(r"\A<details[^>]*>\s*<summary>.*?</summary>\s*(?P<body>.*?)\s*</details>\Z", re.DOTALL)
_DETAILS_TAG = re.compile(r"<details[^>]*>|</details>")
# The Plan's closing block: what is owed once the pull request has merged, one `- ` bullet each.
_AFTER_MERGE = re.compile(r"^### After the merge\s*$", re.MULTILINE)
_CHECKBOX = re.compile(r"^\[[ xX]\]\s+")

# `get` has to tell "no default" from a default of None, and None is a default a caller wants.
_RAISE = object()


class MissingSection(KeyError):
    """A section name not present in the body; str() is the message, not a repr."""

    def __str__(self) -> str:
        return self.args[0]


def _nested(text: str) -> bool:
    """True when every `</details>` in `text` closes a `<details>` opened inside it, and none is left open."""
    depth = 0
    for tag in _DETAILS_TAG.findall(text):
        depth += -1 if tag.startswith("</") else 1
        if depth < 0:
            return False
    return depth == 0


def _unfolded(name: str, body: str) -> str:
    """The section body without the fold `render` puts round a plan; every reader sees this.

    Any `<details>` whose first element is a `<summary>` and which closes at the end of the plan is
    that fold, however it was reshaped on the way out and back. A plan that merely opens with a
    block of its own leaves its own tags unbalanced inside the match, and is left as it is.
    """
    text = body.strip("\n")
    if name != "Plan":
        return text
    found = _FOLD.match(text)
    if found and _nested(found.group("body")):
        return found.group("body").strip("\n")
    return text


def _folded(name: str, body: str) -> str:
    """The section body as it is written: a plan with content sits in a collapsed block on GitHub."""
    text = _unfolded(name, body)
    if name != "Plan" or not text:
        return text
    return f"{FOLD_OPEN}\n\n{text}\n\n{FOLD_CLOSE}"


def parse(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (preamble, [(name, body)]). Bodies exclude the heading, outer blank lines, and the fold."""
    preamble, parsed, current, buf = [], [], None, []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line in lines:
        match = HEADING.match(line)
        if match:
            if current is None:
                preamble = buf
            else:
                parsed.append((current, _unfolded(current, "\n".join(buf))))
            current, buf = match.group(1), []
        else:
            buf.append(line)
    if current is None:
        preamble = buf
    else:
        parsed.append((current, _unfolded(current, "\n".join(buf))))
    return "\n".join(preamble).strip("\n"), parsed


def render(preamble: str, parsed: list[tuple[str, str]], fold: bool = True) -> str:
    """The body as text; `fold` off leaves the plan bare, which is what a model is handed to edit."""
    parts = [preamble] if preamble else []
    for name, body in parsed:
        content = _folded(name, body) if fold else _unfolded(name, body)
        parts.append(f"### {name}\n\n{content}".rstrip("\n"))
    return "\n\n".join(parts) + "\n"


def bare(text: str) -> str:
    """`text` with the plan out of its fold: the body as the model reads it and drafts it back."""
    preamble, parsed = parse(text)
    return render(preamble, parsed, fold=False)


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


def after_merge(body: str) -> list[str]:
    """The items of the Plan's closing "After the merge" block, in order; empty without one.

    The block is `- ` bullets, one item each; a checkbox a plan puts in front of one is not part of
    the item, ticked or not, since the log is where an item is marked done.
    """
    plan = get(body, "Plan", "")
    match = _AFTER_MERGE.search(plan)
    if match is None:
        return []
    bullets = [line[2:].strip() for line in plan[match.end() :].splitlines() if line.startswith("- ")]
    return [_CHECKBOX.sub("", bullet) for bullet in bullets]
