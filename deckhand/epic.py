"""A feature: an option of the project's Feature field, the stories that hold it, and what is left of it in dates.

The option's place in the field is the feature's place in the pipeline, and a story of any repository
belongs to it by its Feature value. This is the one place that opens one, adds a story to one, and
reads one back, as lines for a person or as JSON for a report that is not a session.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from deckhand import board, draft, fleet, gh, throughput
from deckhand.cli import command
from deckhand.config import Settings
from deckhand.step import Refusal, issue_ref, ref_label, resolved_settings

ACTIONS = ("open", "add", "list", "forecast")
REF_FORM = "a story is owner/name#M, or M for this repository"
COLOR = "GRAY"
# A fixed draw, so two forecasts of an unchanged board give a report the same dates.
SEED = 0


def _today() -> date:
    return date.today()


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=ACTIONS, help="what to do")
    parser.add_argument(
        "refs", nargs="*", help="open: the name; add: the feature, then each story; forecast: the feature"
    )
    parser.add_argument("--about", default="", help="open: what the feature delivers, in one plain sentence")
    parser.add_argument("--json", action="store_true", help="list and forecast: print JSON instead of lines")


def _key(found: tuple[str, int]) -> tuple[str, int]:
    """A reference as GitHub matches one, whose repository part ignores case."""
    return found[0].lower(), found[1]


def _ref(value: str, repo: str) -> tuple[str, int]:
    try:
        return _key(issue_ref(value, repo))
    except ValueError as error:
        raise Refusal(f"{REF_FORM}, got {value!r}") from error


def _field(settings: Settings) -> dict | None:
    """The Feature field as the project reports it, or None before any feature was opened."""
    found = next((field for field in gh.project_fields(settings) if field["name"] == board.FEATURE), None)
    if found is not None and found["dataType"] != "SINGLE_SELECT":
        raise Refusal(f"{board.FEATURE} is {found['dataType']}, not a single select; fix it in the project settings")
    return found


def _options(settings: Settings) -> tuple[str, list[dict]]:
    found = _field(settings)
    if found is None:
        raise Refusal(f"no {board.FEATURE} field yet; open a feature with epic open")
    return str(found["id"]), found["options"]


def _find(options: list[dict], named: str) -> dict:
    """The option `named` names: by its id, by its name in any case, or by the start of one name alone."""
    wanted = " ".join(named.split()).casefold()
    exact = [option for option in options if option["id"] == named or option["name"].casefold() == wanted]
    found = exact or [option for option in options if wanted and option["name"].casefold().startswith(wanted)]
    if len(found) == 1:
        return found[0]
    if found:
        raise Refusal(f"{named} matches {' and '.join(option['name'] for option in found)}; name one in full")
    names = ", ".join(option["name"] for option in options) or "none"
    raise Refusal(f"no feature matches {named}; the features are {names}")


def _open(args: argparse.Namespace) -> int:
    name = " ".join(" ".join(args.refs).split())
    if not name:
        raise Refusal('epic open takes a name: epic open "<name>" --about "<sentence>"')
    about = " ".join(args.about.split())
    if not about:
        raise Refusal("--about says what the feature delivers, in one plain sentence")
    settings = resolved_settings()
    found = _field(settings)
    new = {"name": name, "color": COLOR, "description": about}
    if found is None:
        gh.create_single_select(settings, board.FEATURE, [new])
        print(f"Created the {board.FEATURE} field with {name}")
        return 0
    taken = next((option for option in found["options"] if option["name"].casefold() == name.casefold()), None)
    if taken is not None:
        raise Refusal(f"{taken['name']} is already a feature; add stories to it with epic add")
    # The write replaces the whole list, and an option sent without its id clears it from every story.
    kept = [
        {"id": option["id"], "name": option["name"], "color": option["color"], "description": option["description"]}
        for option in found["options"]
    ]
    gh.update_single_select(settings, str(found["id"]), [*kept, new])
    print(f"Opened feature {name}, last of {len(kept) + 1}")
    return 0


def _story(held: dict[tuple[str, int], fleet.Story], ref: tuple[str, int], repo: str) -> fleet.Story:
    named = ref_label(*ref, repo)
    story = held.get(ref)
    if story is None:
        raise Refusal(f"{named} is not on the board; a story joins a feature from the board")
    if not fleet.touched(story):
        raise Refusal(f"{named} is not a deckhand story; write one with new")
    if story.dropped:
        raise Refusal(f"{named} was closed as not planned or a duplicate, so it is no piece of a feature")
    return story


def _add(args: argparse.Namespace, repo: str) -> int:
    if len(args.refs) < 2:
        raise Refusal("epic add takes the feature, then each story: epic add <feature> <story>...")
    refs = list(dict.fromkeys(_ref(value, repo) for value in args.refs[1:]))
    settings = resolved_settings()
    field, options = _options(settings)
    option = _find(options, args.refs[0])
    held = {_key(story.key): story for story in fleet.load(settings, archived=True)}
    stories = [(ref_label(*ref, repo), _story(held, ref, repo)) for ref in refs]
    project = gh.project_id(settings)
    name = option["name"]
    for named, story in stories:
        if story.feature == option["id"]:
            print(f"{named} already in {name}", flush=True)
            continue
        flags = ("--project-id", project, "--field-id", field, "--single-select-option-id", option["id"])
        gh.run("project", "item-edit", "--id", story.item, *flags)
        moved = f"moved from {story.feature_name} to" if story.feature else "joined"
        print(f"{named} {moved} {name}", flush=True)
    return 0


def _row(read: fleet.Fleet, option: dict) -> dict:
    """One feature and its pieces; archived stories count, because the board archives finished work."""
    mine = fleet.members([*read.stories, *read.archived], option["id"])
    done = sum(1 for story in mine if story.closed)
    return {
        "epic": option["id"],
        "title": option["name"],
        "about": option["description"],
        "closed": bool(mine) and done == len(mine),
        "pieces": len(mine),
        "done": done,
        "drafts": sum(1 for story in mine if not story.closed and draft.is_draft(story.issue.body)),
    }


def _list(args: argparse.Namespace) -> int:
    settings = resolved_settings()
    found = _field(settings)
    options = found["options"] if found else []
    read = fleet.read(settings) if options else None
    rows = [_row(read, option) for option in options] if read else []
    if args.json:
        print(json.dumps(rows))
        return 0
    for row in rows:
        about = f" {row['about']}" if row["about"] else ""
        print(f"{row['title']}: {row['done']} of {row['pieces']} pieces done, {row['drafts']} drafts.{about}")
    if not rows:
        print("no features yet; open one with epic open")
    return 0


def _on(weeks: int | None) -> str | None:
    return None if weeks is None else (_today() + timedelta(days=7 * weeks)).isoformat()


def _dated(found: throughput.Outlook) -> dict:
    return {
        "floor": _on(found.floor_weeks),
        "commitment": _on(found.commitment_weeks),
        "worst": _on(found.worst_weeks),
        "pace": found.pace,
        "weeks": found.weeks,
        "per_week": found.per_week,
        "unsplit": found.unsplit,
        "split_size": round(found.split_size, 2),
        "split_basis": found.split_basis,
        "reason": found.reason,
    }


def _forecast(args: argparse.Namespace) -> int:
    if len(args.refs) != 1:
        raise Refusal("epic forecast takes one feature: epic forecast <feature>")
    settings = resolved_settings()
    option = _find(_options(settings)[1], args.refs[0])
    read = fleet.read(settings)
    found = throughput.outlook(read, option["id"], _today(), seed=SEED)
    if args.json:
        print(json.dumps({**_row(read, option), **_dated(found)}))
        return 0
    print(f"## Forecast: {option['name']}")
    for line in throughput.rows(found):
        print(line)
    return 0


@command("epic", _configure)
def run(args: argparse.Namespace) -> int:
    """Open a feature, add stories to it, list every feature in pipeline order, or forecast one in dates."""
    if args.action == "open":
        return _open(args)
    if args.action == "add":
        return _add(args, gh.repo_slug().lower())
    with gh.cached():
        if args.action == "list":
            return _list(args)
        return _forecast(args)
