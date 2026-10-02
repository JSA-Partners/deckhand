"""An epic: the issue a feature's stories roll up to, and what is left of it in dates.

An epic is an issue carrying the `epic` label and never a story: its stories are its sub-issues,
from any repository of the project, and its place on the board is its place in the pipeline. This
is the one place that opens one, adds a story to one, and reads one back, as lines for a person or
as JSON for a report that is not a session.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, timedelta

from deckhand import board, draft, fleet, forecast, gh, issue
from deckhand.cli import command
from deckhand.step import Refusal, issue_ref, ref_label, resolved_settings

ACTIONS = ("open", "add", "list", "forecast")
REF_FORM = "an epic or a story is owner/name#M, or M for this repository"
# A fixed draw, so two forecasts of an unchanged board give a report the same dates.
SEED = 0


def _today() -> date:
    return date.today()


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=ACTIONS, help="what to do")
    parser.add_argument("refs", nargs="*", help="open: the title; add: the epic, then each story; forecast: the epic")
    parser.add_argument("--about", default="", help="open: what the epic delivers, in one plain sentence")
    parser.add_argument("--json", action="store_true", help="list and forecast: print JSON instead of lines")
    parser.add_argument("--sessions", type=int, help="forecast: stories to run at once; defaults to what history shows")


def _ref(value: str, repo: str) -> tuple[str, int]:
    try:
        return issue_ref(value, repo)
    except ValueError as error:
        raise Refusal(f"{REF_FORM}, got {value!r}") from error


def _open(args: argparse.Namespace, repo: str) -> int:
    title = " ".join(" ".join(args.refs).split())
    if not title:
        raise Refusal('epic open takes a title: epic open "<title>" --about "<sentence>"')
    if not args.about.strip():
        raise Refusal("--about says what the epic delivers, in one plain sentence")
    settings = resolved_settings()
    number, url = issue.create(repo, title, args.about.strip() + "\n", label=issue.EPIC)
    print(f"Opened epic #{number} {url}", flush=True)
    board.add(settings, url)
    print("Added to the board", flush=True)
    return 0


def _add(args: argparse.Namespace, repo: str) -> int:
    if len(args.refs) < 2:
        raise Refusal("epic add takes the epic, then each story: epic add <epic> <story>...")
    where, number = _ref(args.refs[0], repo)
    stories = [_ref(value, repo) for value in args.refs[1:]]
    named = ref_label(where, number, repo)
    if issue.EPIC[0] not in issue.view(where, number).labels:
        raise Refusal(f"{named} is not an epic; open one with epic open")
    joined: set[tuple[str, int]] = set()
    for story in stories:
        labels = issue.view(*story).labels
        if issue.EPIC[0] in labels:
            raise Refusal(f"{ref_label(*story, repo)} is an epic, and an epic is never a story")
        if issue.LABEL[0] not in labels:
            raise Refusal(f"{ref_label(*story, repo)} is not a deckhand story; write one with new")
        if issue.parent(*story) == (where, number):
            joined.add(story)
        # Looked up again by the write, but resolved here so a story the API cannot find leaves the epic alone.
        gh.issue_id(*story)
    for story in stories:
        if story in joined:
            print(f"{ref_label(*story, repo)} already in {named}", flush=True)
            continue
        issue.add_sub_issue(where, number, story)
        print(f"{ref_label(*story, repo)} joined {named}", flush=True)
    return 0


def _row(read: fleet.Fleet, epic: fleet.Story) -> dict:
    """One epic and its pieces; archived stories count, because the board archives finished work."""
    mine = [story for story in fleet.members([*read.stories, *read.archived], epic.key) if not story.dropped]
    return {
        "epic": f"{epic.repo}#{epic.number}",
        "title": epic.title,
        "about": epic.issue.body.strip(),
        "closed": epic.closed,
        "pieces": len(mine),
        "done": sum(1 for story in mine if story.closed),
        "drafts": sum(1 for story in mine if not story.closed and draft.is_draft(story.issue.body)),
    }


def _list(args: argparse.Namespace, repo: str) -> int:
    read = fleet.read(resolved_settings())
    found = fleet.epics(read.stories)
    if args.json:
        print(json.dumps([_row(read, epic) for epic in found]))
        return 0
    for epic in found:
        row = _row(read, epic)
        label = ref_label(epic.repo, epic.number, repo)
        print(f"{label} {epic.title}: {row['done']} of {row['pieces']} pieces done, {row['drafts']} drafts")
    if not found:
        print("no epics on the board")
    return 0


def _on(hours: float) -> str:
    return (_today() + timedelta(days=math.ceil(hours / forecast.HOURS_A_DAY))).isoformat()


def _dated(found: forecast.Outlook | None) -> dict:
    if found is None:
        nothing = {"floor": None, "commitment": None, "worst": None}
        return {**nothing, "samples": 0, "thin": True, "growth": 1.0, "idle": 0.0, "pace": None}
    return {
        "floor": _on(found.floor),
        "commitment": _on(found.commitment),
        "worst": _on(found.worst),
        "samples": found.samples,
        "thin": found.thin,
        "growth": round(found.growth, 2),
        "idle": round(found.idle, 2),
        "pace": found.pace,
    }


def _forecast(args: argparse.Namespace, repo: str) -> int:
    if len(args.refs) != 1:
        raise Refusal("epic forecast takes one epic: epic forecast <epic>")
    if args.sessions is not None and args.sessions < 1:
        raise Refusal(f"--sessions is how many stories run at once, so at least 1, got {args.sessions}")
    key = _ref(args.refs[0], repo)
    read = fleet.read(resolved_settings())
    # The board archives finished work, and a finished epic is still asked about.
    every = fleet.epics([*read.stories, *read.archived])
    epic = next((story for story in every if story.key == key), None)
    if epic is None:
        raise Refusal(f"{ref_label(*key, repo)} is not an epic on the board")
    at_once, source = forecast.parallel(args.sessions, read, live=1)
    if args.json:
        found = forecast.outlook(read, at_once, scope=key, seed=SEED)
        print(json.dumps({**_row(read, epic), "at_once": at_once, **_dated(found)}))
        return 0
    print(f"## Forecast: {epic.title}")
    for line in forecast.rows(read, at_once, source, scope=key, seed=SEED):
        print(line)
    return 0


@command("epic", _configure)
def run(args: argparse.Namespace) -> int:
    """Open an epic, add stories to it, list every epic in board order, or forecast one in dates."""
    repo = gh.repo_slug()
    if args.action == "open":
        return _open(args, repo)
    if args.action == "add":
        return _add(args, repo)
    with gh.cached():
        if args.action == "list":
            return _list(args, repo)
        return _forecast(args, repo)
