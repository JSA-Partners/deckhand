"""The issues a story names or is linked to, each with its state and column, read in one query.

A session checking a plan against the code asks which of the stories it names have landed. The
board query holds those answers, so the briefing prints them rather than leaving a `gh` loop to
each session.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass

from deckhand import config, fleet, gh, issue
from deckhand.config import Settings
from deckhand.step import reason, ref_label

LIMIT = 25
STOP = frozenset("with from that this when into only their them then than what have each more over about".split())
SHARED = 2
_FENCE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN = re.compile(r"`[^`]*`")
_REFERENCE = re.compile(r"(?<![\w/#.&-])([\w.-]+/[\w.-]+)?#([1-9][0-9]{0,8})\b")
_FIELDS = (
    "number title state projectItems(first:20){ nodes{ project{ number } "
    'fieldValueByName(name:"Status"){ ... on ProjectV2ItemFieldSingleSelectValue{ name } } } }'
)


@dataclass(frozen=True)
class Related:
    """One issue or pull request as the briefing prints it."""

    repo: str
    number: int
    state: str
    status: str | None
    title: str


def naming(body: str, repo: str) -> Iterator[tuple[str, list[tuple[str, int]]]]:
    """Each line of `body` outside fenced code, with the issues it names outside code spans."""
    fence = ""
    for line in body.splitlines():
        opened = _FENCE.match(line)
        if opened and fence in ("", opened.group(1)):
            fence = "" if fence else opened.group(1)
            continue
        if not fence:
            found = _REFERENCE.findall(_CODE_SPAN.sub("", line))
            yield line, [(repo if where.lower() == repo.lower() else where or repo, int(n)) for where, n in found]


def referenced(body: str, repo: str) -> list[tuple[str, int]]:
    """Every issue `body` names as `#N` or `owner/name#N`, once each in order, outside fenced code and code spans."""
    found: dict[tuple[str, int], None] = {}
    for _, keys in naming(body, repo):
        for key in keys:
            found.setdefault(key)
    return list(found)


def read(
    settings: Settings, repo: str, number: int, body: str, skip: frozenset[tuple[str, int]] = frozenset()
) -> list[Related]:
    """What the body names, what the story waits on and what waits on it, less the story and `skip`."""
    keys = dict.fromkeys(referenced(body, repo))
    for where, other, _ in issue.blockers(repo, number) + issue.blocking(repo, number):
        keys.setdefault((where, other))
    for key in (repo, number), *skip:
        keys.pop(key, None)
    return _fetch(settings, list(keys)) if keys else []


def _fetch(settings: Settings, keys: list[tuple[str, int]]) -> list[Related]:
    """Every key in one query, one aliased `issueOrPullRequest` per number under its repository."""
    repos: dict[str, list[int]] = {}
    for where, number in keys:
        repos.setdefault(where, []).append(number)
    parts = []
    for index, (where, numbers) in enumerate(repos.items()):
        owner, name = gh.split_repo(where)
        asked = " ".join(
            f"i{n}: issueOrPullRequest(number:{n}){{ ... on Issue{{ {_FIELDS} }} ... on PullRequest{{ {_FIELDS} }} }}"
            for n in numbers
        )
        parts.append(f'r{index}: repository(owner:"{owner}", name:"{name}"){{ {asked} }}')
    try:
        data = gh.graphql("query{ " + " ".join(parts) + " }")[0]["data"]
    except gh.GhError as error:
        # An unresolvable number fails the call but still returns the rest under data.
        try:
            data = json.loads(error.stdout)["data"]
        except (ValueError, KeyError, TypeError):
            raise error from None
        if not isinstance(data, dict):
            raise error from None
    found = []
    for index, (where, numbers) in enumerate(repos.items()):
        answered = data.get(f"r{index}") or {}
        for number in numbers:
            node = answered.get(f"i{number}")
            if node:
                found.append(_related(settings, where, node))
    return found


def _related(settings: Settings, repo: str, node: dict) -> Related:
    """One answered node, its column taken from its item on the configured project."""
    status = next(
        (
            (item.get("fieldValueByName") or {}).get("name")
            for item in (node.get("projectItems") or {}).get("nodes") or []
            if (item.get("project") or {}).get("number") == settings.project
        ),
        None,
    )
    title = " ".join((node.get("title") or "").split())
    return Related(repo, int(node["number"]), str(node.get("state") or "").lower(), status, title)


def lines(found: list[Related], repo: str) -> list[str]:
    """One line each, by repository then number, capped at `LIMIT` with a count of the rest."""
    ordered = sorted(found, key=lambda item: (item.repo != repo, item.repo, item.number))
    shown = [
        f"{'' if item.repo == repo else item.repo}#{item.number} {item.state} "
        f"{item.status or 'off the board'} {item.title}"
        for item in ordered[:LIMIT]
    ]
    if len(ordered) > LIMIT:
        shown.append(f"and {len(ordered) - LIMIT} more")
    return shown


def _words(title: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", title.lower()) if len(word) >= 4 and word not in STOP}


def similar(stories: list[fleet.Story], title: str, skip: tuple[str, int] | None = None) -> list[fleet.Story]:
    """The open stories the process owns whose titles share `SHARED` significant words with `title`.

    A duplicate looks like this, and the board holds every repository, so a story parked elsewhere
    is seen before anything is written.
    """
    mine = _words(title)
    own = fleet.folded(skip) if skip else None
    return [
        story
        for story in stories
        if not story.closed
        and fleet.touched(story)
        and fleet.folded(story.key) != own
        and len(mine & _words(story.title)) >= SHARED
    ]


def similar_lines(stories: list[fleet.Story], title: str, repo: str, skip: tuple[str, int] | None = None) -> list[str]:
    """`Similar: <reference> <column> <title>` per match, the reference as a session in `repo` reads it."""
    return [
        f"Similar: {ref_label(story.repo, story.number, repo)} {story.status or 'off the board'} {story.title}"
        for story in similar(stories, title, skip)
    ]


def resembling(title: str, repo: str | None = None, skip: tuple[str, int] | None = None) -> list[str]:
    """The Similar lines for `title` from the whole board, `Similar: none`, or why they could not be read."""
    try:
        here = repo or gh.repo_slug()
        return similar_lines(fleet.load(config.load()), title, here, skip) or ["Similar: none"]
    except Exception as error:
        return [f"Similar: unavailable ({reason(error)})"]


def block(repo: str, number: int, story: issue.Issue, skip: frozenset[tuple[str, int]], resemble: bool) -> None:
    """Print what the story names and is linked to, and with `resemble` the open stories its title looks like.

    Each read sits inside its own try, so a settings or board failure costs one line, never the briefing.
    """
    try:
        found = read(config.load(), repo, number, story.body, skip)
    except Exception as error:
        print(f"Related: unavailable ({reason(error)})")
    else:
        print("Related:" if found else "Related: none")
        for line in lines(found, repo):
            print(f"  {line}")
    if resemble:
        for line in resembling(story.title, repo, (repo, number)):
            print(line)
