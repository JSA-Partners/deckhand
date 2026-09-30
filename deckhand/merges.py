"""What to merge first: every open pull request, ranked, and which of the others will need updating after.

Two pull requests that change a common path cannot both merge as they stand: whichever goes second
has to bring its branch up to date first. Everything here comes from the one fleet read, so nothing
is fetched and the same fleet always ranks the same.
"""

from __future__ import annotations

from dataclasses import dataclass

from deckhand import fleet, issue, order
from deckhand.step import ref_label


def _name(repo: str) -> str:
    return repo.partition("/")[2] or repo


def _checks(pull: issue.PullRequest) -> str:
    if pull.failed_checks:
        return "failed: " + ", ".join(pull.failed_checks)
    return f"{pull.pending_checks} running" if pull.pending_checks else "passed"


def _main(pull: issue.PullRequest) -> str:
    return "unknown" if pull.behind is None else "behind" if pull.behind else "current"


def _files(count: int) -> str:
    return f"{count} {'file' if count == 1 else 'files'}"


@dataclass(frozen=True)
class _Pull:
    """One open pull request and every story of the process it closes."""

    pull: issue.PullRequest
    repo: str
    stories: tuple[int, ...]
    freed: int

    @property
    def ready(self) -> bool:
        return not self.pull.failed_checks and not self.pull.pending_checks and self.pull.behind is False


def _pulls(read: fleet.Fleet) -> list[_Pull]:
    """The open pull requests of the process's stories, each once however many stories it closes."""
    touched = {story.key: story for story in read.stories if fleet.touched(story)}
    grouped: dict[str, list[fleet.Key]] = {}
    for key, pull in sorted(read.pulls.items()):
        if key in touched:
            grouped.setdefault(pull.url, []).append(key)
    waits = order.waiting(read.blockers)
    points = {story.key: story.points or 0 for story in read.stories}
    found = []
    for keys in grouped.values():
        below = set().union(*(order.downstream(key, waits) for key in keys)) - set(keys)
        freed = sum(points.get(key, 0) for key in below)
        found.append(_Pull(read.pulls[keys[0]], keys[0][0], tuple(number for _, number in keys), freed))
    return found


def _shared(pulls: list[_Pull]) -> dict[str, dict[str, int]]:
    """For each pull request, the others in its repository changing a path it changes, and how many, by URL."""
    found: dict[str, dict[str, int]] = {one.pull.url: {} for one in pulls}
    for one in pulls:
        for two in pulls:
            common = len(set(one.pull.files) & set(two.pull.files))
            if two.pull.url != one.pull.url and two.repo == one.repo and common:
                found[one.pull.url][two.pull.url] = common
    return found


def rows(read: fleet.Fleet, repo: str) -> list[str]:
    """One row per open pull request in the order to merge them, then the line saying what goes first.

    Only the process's stories count, and a pull request closing several of them is one row. One
    ready to merge, its checks passed and current with main, goes before one that is not. Among
    equals, the one freeing more points of waiting work goes first, then the one sharing files with
    fewer others, so the fewest branches need updating after it.
    """
    pulls = _pulls(read)
    if not pulls:
        return ["  nothing in review"]
    shared = _shared(pulls)
    ranked = sorted(pulls, key=lambda one: (not one.ready, -one.freed, len(shared[one.pull.url]), one.pull.number))
    named = {one.pull.url: ref_label(one.repo, one.pull.number, repo) for one in pulls}
    table = [
        "| # | Repo | PR | Checks | Main | Unblocks | Shares files with |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for one in ranked:
        shares = ", ".join(f"{named[url]} ({_files(count)})" for url, count in shared[one.pull.url].items())
        stories = ", ".join(str(number) for number in one.stories)
        unblocks = f"{one.freed} pts" if one.freed else "-"
        table.append(
            f"| {stories} | {_name(one.repo)} | #{one.pull.number} | {_checks(one.pull)} | {_main(one.pull)} | "
            f"{unblocks} | {shares or '-'} |"
        )
    first = ranked[0]
    after = {named[url]: count for url, count in shared[first.pull.url].items()}
    return [*table, "", _closing(named[first.pull.url], first.ready, after)]


def _closing(first: str, ready: bool, after: dict[str, int]) -> str:
    """The line under the table: what to merge first and which of the others update after it."""
    head = f"Merge {first} first" if ready else f"Nothing is ready to merge yet, and {first} goes first once it is"
    if not after:
        return f"{head}; no other open pull request changes its files."
    if len(after) == 1:
        ((other, count),) = after.items()
        return f"{head}; {other} shares {_files(count)} with it and updates after."
    listed = [f"{other} ({_files(count)})" for other, count in after.items()]
    return f"{head}; {', '.join(listed[:-1])} and {listed[-1]} share files with it and update after."
