"""Slugs, branch names, and the pull request message for the story process.

The repository squashes with the pull request title and body as the commit message, so the title is
a commit subject and the body is a commit body: the subject has a length a reader can scan, and the
body is the story as one wrapped paragraph over the footers a tool reads.
"""

from __future__ import annotations

import re
import textwrap

from deckhand.config import Settings

SUBJECT = 72  # the longest commit subject a git log, a terminal, and GitHub all show whole
BANG = "!"  # marks a breaking change, and is reserved in every title so a late one cannot overflow
WRAP = 100  # the commit body's width, the same one the project's own prose is written to

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_NUMBER_RE = re.compile(r"^[0-9]+$")


def slug(title: str) -> str:
    """Lowercase `title`, map runs outside `[a-z0-9]` to one hyphen, trim, and keep the first four words."""
    collapsed = _NON_ALNUM.sub("-", title.lower()).strip("-")
    if not collapsed:
        raise ValueError("title has no usable characters")
    return "-".join(collapsed.split("-")[:4])


def branch_name(settings: Settings, kind: str, number: str | int, title: str) -> str:
    """`<kind>/<number>-<slug>`; validates `kind` against `settings.kinds` and `number` as an integer."""
    if kind not in settings.kinds:
        raise ValueError(f"unknown kind {kind!r} (one of: {' '.join(settings.kinds)})")
    if not _NUMBER_RE.match(str(number)):
        raise ValueError(f"issue number must be an integer, got {number!r}")
    try:
        result = slug(title)
    except ValueError:
        result = ""
    if not result or not _SLUG_RE.match(result):
        raise ValueError(f"title {title!r} yields no usable slug")
    return f"{kind}/{number}-{result}"


def pr_title(settings: Settings, kind: str, title: str, breaking: bool = False) -> str:
    """`<kind>[!]: <title>` verbatim; validates `kind` and the length of the subject it makes.

    The bang counts against the limit whether or not it is asked for, so the title a person is shown
    before they choose to mark the change breaking is never the title that is then refused.
    """
    if kind not in settings.kinds:
        raise ValueError(f"unknown kind {kind!r} (one of: {' '.join(settings.kinds)})")
    if len(f"{kind}{BANG}: {title}") > SUBJECT:
        raise ValueError("title too long for a commit subject; shorten the issue title")
    return f"{kind}{BANG if breaking else ''}: {title}"


def pr_body(number: str | int, story: str, breaking: str | None = None) -> str:
    """The story as one wrapped paragraph, then the footer block; `number` must be an integer.

    The story is rewrapped rather than copied, because the issue's own line breaks are the width of
    an issue body and the commit body is read in a git log. A long word is left long: a path or a
    URL broken over two lines is one a reader cannot copy and a tool cannot follow.
    """
    if not _NUMBER_RE.match(str(number)):
        raise ValueError(f"issue number must be an integer, got {number!r}")
    footers = [f"BREAKING CHANGE: {breaking}"] if breaking else []
    footers.append(f"Closes #{number}")
    paragraph = textwrap.fill(" ".join(story.split()), WRAP, break_long_words=False, break_on_hyphens=False)
    return paragraph + "\n\n" + "\n".join(footers) + "\n"
