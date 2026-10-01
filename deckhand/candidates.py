"""What to open next: every story not yet started, the step it needs, and what could collide with it.

A lane plan needs three facts per story that the board alone does not show together: what it waits
on, which stories its text names without an edge between them, and which files its plan touches.
Everything here comes from the one fleet read, so nothing is fetched. The step is re-stated from the
column and the draft state rather than taken from `next`, whose routing wants a branch and a pull
request a board read does not hold.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations

from deckhand import columns, draft, drift, fleet, lint, order, related, sections
from deckhand.step import ref_label

UNSTARTED = (columns.DRAFT, columns.REFINEMENT, columns.READY, columns.BACKLOG)
SHOWN = 3
# A file this many plans name is one every story touches, so pairing on it separates nothing.
SHARED = 3
SHARED_SHOWN = 5
# Long enough for the clause that says why one story names the other, short enough to scan.
LINE = 160


def _name(repo: str) -> str:
    return repo.partition("/")[2] or repo


def _next_step(story: fleet.Story, blockers: list[tuple[str, int, str]]) -> str:
    """The step `next` would route this story to, from its column and whether its body is a draft."""
    if story.status == columns.BACKLOG:
        return "wait" if blockers else "start"
    if draft.is_draft(story.issue.body):
        return "write" if draft.read(story.issue.body)[1] else "settle"
    return "board" if columns.at_least(story.status, columns.READY) else "review"


def _files(story: fleet.Story) -> set[str]:
    """The paths the story's plan names, without their line numbers."""
    plan = sections.get(story.issue.body, "Plan", "")
    return {ref.partition(":")[0] for ref in drift.references(plan)}


def _named(story: fleet.Story, read: fleet.Fleet, repo: str) -> dict[str, str]:
    """Open board stories the body names that are no blocker edge either way, with the first line naming each."""
    open_keys = {other.key for other in read.stories if not other.closed and other.status != columns.DONE}
    linked = {(where, number) for where, number, _ in read.blockers.get(story.key) or []}
    linked |= {key for key, holds in read.blockers.items() if any((w, n) == story.key for w, n, _ in holds)}
    preamble, parsed = sections.parse(story.issue.body)
    text = "\n".join([preamble, *(lint.within_scope(body) if name == "Scope" else body for name, body in parsed)])
    named: dict[str, str] = {}
    for line, keys in related.naming(text, story.repo):
        for key in keys:
            if key in open_keys and key not in linked and key != story.key:
                named.setdefault(ref_label(*key, repo), _trimmed(line))
    return named


def _trimmed(line: str) -> str:
    text = " ".join(line.split()).lstrip("-* ")
    return text if len(text) <= LINE else text[:LINE].rstrip() + "..."


def _shared(stories: list[fleet.Story], files: dict[fleet.Key, set[str]]) -> Counter[tuple[str, str]]:
    """`(repo, path)` named by at least `SHARED` plans, with how many name it."""
    named = Counter((story.repo, path) for story in stories for path in files[story.key])
    return Counter({key: count for key, count in named.items() if count >= SHARED})


def _overlaps(stories: list[fleet.Story], unstarted: set[fleet.Key], read: fleet.Fleet, repo: str) -> list[str]:
    """The shared files, then one line per pair that could run at once and whose plans share a file."""
    files = {story.key: _files(story) for story in stories}
    shared = _shared(stories, files)
    waits = order.waiting(read.blockers)
    lines = []
    if shared:
        ranked = sorted(shared.items(), key=lambda item: (-item[1], item[0]))
        named = ", ".join(
            f"{path if where == repo else f'{where}:{path}'} ({count} plans)"
            for (where, path), count in ranked[:SHARED_SHOWN]
        )
        rest = f" and {len(ranked) - SHARED_SHOWN} more" if len(ranked) > SHARED_SHOWN else ""
        lines.append(f"Shared: {named}{rest}")
    pairs = []
    for one, two in combinations(stories, 2):
        if one.repo != two.repo or (one.key not in unstarted and two.key not in unstarted):
            continue
        if two.key in order.downstream(one.key, waits) or one.key in order.downstream(two.key, waits):
            continue
        common = sorted(path for path in files[one.key] & files[two.key] if (one.repo, path) not in shared)
        if not common:
            continue
        shown = ", ".join(common[:SHOWN])
        rest = f" and {len(common) - SHOWN} more" if len(common) > SHOWN else ""
        pair = f"{ref_label(one.repo, one.number, repo)} and {ref_label(two.repo, two.number, repo)}"
        pairs.append(f"Overlap: {pair}: {shown}{rest}")
    return [*lines, *(pairs or ["Overlap: none"])]


def rows(read: fleet.Fleet, repo: str) -> list[str]:
    """The candidates table and, under it, the files most plans name and every pair that could collide."""
    mine = [story for story in read.stories if fleet.touched(story) and not story.closed]
    found = [story for story in mine if story.status in UNSTARTED]
    if not found:
        return ["  nothing waiting to start"]
    table = [
        "| # | Repo | Title | Status | Pts | Next | Waits on | Named, no edge | Files |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines = []
    for story in found:
        blockers = read.blockers.get(story.key) or []
        waits = ", ".join(ref_label(where, number, repo) for where, number, _ in blockers) or "-"
        names = _named(story, read, repo)
        label = ref_label(story.repo, story.number, repo)
        lines += [f"Named: {label} names {other}: {line}" for other, line in names.items()]
        named = ", ".join(names) or "-"
        points = "-" if story.points is None else str(story.points)
        title = story.title.replace("|", "\\|")
        table.append(
            f"| {story.number} | {_name(story.repo)} | {title} | {story.status} | {points} | "
            f"{_next_step(story, blockers)} | {waits} | {named} | {len(_files(story))} |"
        )
    building = [story for story in mine if story.status == columns.IN_PROGRESS]
    return [*table, "", *lines, *_overlaps(found + building, {story.key for story in found}, read, repo)]
