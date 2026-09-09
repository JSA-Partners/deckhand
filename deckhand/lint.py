"""Lint a story body against the one contract every story meets.

A story body has five sections, once each and in this order: Story, Scope, Acceptance Criteria,
Plan, Notes. There is one stage: a body either meets the contract or it does not.
"""

from __future__ import annotations

import re

from deckhand import sections
from deckhand.config import BODY_LIMIT
from deckhand.step import Refusal

_STORY_FORM = re.compile(r"^As an? .+, I want .+, so that .+$")
_STORY_TWO_SENTENCES = re.compile(r"\.\s+[A-Za-z0-9]")
_SCOPE_IN = re.compile(r"^#### In\s*$")
_SCOPE_OUT = re.compile(r"^#### Out\s*$")
_GWT = re.compile(r"^- given .+when .+then .+", re.IGNORECASE)
# What a criterion says when it is about the process rather than the behaviour. Named in the words
# a story uses, never in a tool's: which command a project runs is the project's business, and a
# rule that listed them would be a list of every build tool there is.
_PROCESS_LINE = re.compile(
    r"(CI (passes|is green)|lint(er)? passes|tests? (pass|are green)|"
    r"(PR|pull request) is (open|opened|merged|approved|reviewed)|"
    r"code review passes|changes? (are|is) committed)",
    re.IGNORECASE,
)
_SCOPE_EXCLUSION = re.compile(r"(out of scope|not in scope|is not included)", re.IGNORECASE)
_TASK_ONE = re.compile(r"^### Task 1", re.MULTILINE)
_SUB_BULLET_LINE = re.compile(r"^\s+\S")
_SUB_BULLET_LEADING_WS = re.compile(r"^\s+")


def checked(body: str) -> str:
    """`body` as it is written, or a refusal naming every rule it breaks.

    Every step that writes a story body goes through it, and what it lints is the rendered body,
    so the size the limit answers for is the size GitHub is handed, fold and all.
    """
    written = sections.render(*sections.parse(body))
    problems = lint(written)
    if problems:
        raise Refusal("body: " + "; ".join(problems))
    return written


def _heading(lines: list[str], pattern: re.Pattern[str]) -> int | None:
    """The index of the first line matching `pattern`, or None when no line does."""
    return next((i for i, line in enumerate(lines) if pattern.match(line)), None)


def _bullets(lines: list[str], start: int, headings: list[int]) -> list[str]:
    """The `- ` bullets under the subheading at `start`, up to the next subheading in `headings`."""
    end = min((i for i in headings if i > start), default=len(lines))
    return [line for line in lines[start + 1 : end] if line.startswith("- ")]


def _fold_bullets(text: str) -> list[str]:
    """Group each `- ` bullet with its wrapped continuation and indented sub-bullet lines into one line."""
    bullets: list[str] = []
    current = ""
    for line in text.split("\n"):
        if line.startswith("- "):
            if current:
                bullets.append(current)
            current = line
        elif current and _SUB_BULLET_LINE.match(line):
            current += _SUB_BULLET_LEADING_WS.sub(" ", line)
        elif current:
            bullets.append(current)
            current = ""
    if current:
        bullets.append(current)
    return bullets


def lint(text: str) -> list[str]:
    """Return the failing rules against `text` (empty means the body is ok).

    `\\r\\n` is normalized to `\\n` before the size check, so line-ending style doesn't affect the limit.
    """
    fail: list[str] = []

    text = text.replace("\r\n", "\n")
    size = len(text)
    if size > BODY_LIMIT:
        fail.append(f"body is {size} characters; the limit is {BODY_LIMIT}")

    _, parsed = sections.parse(text)
    names = [name for name, _ in parsed]
    for name in sections.SECTIONS:
        count = names.count(name)
        if count == 0:
            fail.append(f"missing section: {name}")
        elif count > 1:
            fail.append(f"duplicate section: {name}")
    seen = [name for i, name in enumerate(names) if name not in names[:i]]
    if seen != [name for name in sections.SECTIONS if name in seen]:
        fail.append("sections are out of order; the order is " + ", ".join(sections.SECTIONS))

    present = set(names)
    if "Story" in present:
        fail.extend(_story(sections.get(text, "Story", "")))
    if "Scope" in present:
        fail.extend(_scope(sections.get(text, "Scope", "")))
    if "Acceptance Criteria" in present:
        fail.extend(_criteria(sections.get(text, "Acceptance Criteria", "")))
    if "Plan" in present and not _TASK_ONE.search(sections.get(text, "Plan", "")):
        fail.append("Plan: no '### Task 1' block")

    return fail


def _story(body: str) -> list[str]:
    """The Story rule: one sentence in the as-a, I-want, so-that form."""
    story = re.sub(r" +", " ", body.replace("\n", " ")).strip()
    if not _STORY_FORM.match(story) or _STORY_TWO_SENTENCES.search(story):
        return ["Story is not one 'As a ..., I want ..., so that ...' sentence"]
    return []


def _scope(body: str) -> list[str]:
    """The Scope rules: both subheadings present, In before Out, and neither one empty."""
    fail: list[str] = []
    lines = body.split("\n")
    in_at = _heading(lines, _SCOPE_IN)
    out_at = _heading(lines, _SCOPE_OUT)
    headings = [i for i in (in_at, out_at) if i is not None]

    if in_at is None:
        fail.append("Scope: no '#### In' subheading")
    elif not _bullets(lines, in_at, headings):
        fail.append("Scope: '#### In' has no bullets")
    if out_at is None:
        fail.append("Scope: no '#### Out' subheading")
    elif not _bullets(lines, out_at, headings):
        fail.append("Scope: '#### Out' must be non-empty; it is the fence scope creep is measured against")
    if in_at is not None and out_at is not None and in_at > out_at:
        fail.append("Scope: '#### In' must come before '#### Out'")
    return fail


def _criteria(body: str) -> list[str]:
    """The Acceptance Criteria rules: at least one bullet, each Given/When/Then and about behaviour."""
    fail: list[str] = []
    bullets = _fold_bullets(body)
    if not bullets:
        fail.append("Acceptance Criteria has no bullets")
    for line in bullets:
        short = line[2:][:40]
        norm = re.sub(r" {2,}", " ", line.replace("*", "").replace("_", "").replace(":", " "))
        if not _GWT.match(norm):
            fail.append(f"acceptance bullet is not Given/When/Then: {short}")
        if _PROCESS_LINE.search(line):
            fail.append(f"acceptance bullet is a process line: {short}")
        if _SCOPE_EXCLUSION.search(line):
            fail.append(f"acceptance bullet is a scope exclusion; move it to Scope > Out: {short}")
    return fail
