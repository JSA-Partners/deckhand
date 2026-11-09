"""Every disagreement the board read names: what no step could have written, and what puts it right."""

from __future__ import annotations

from dataclasses import dataclass

from deckhand import columns, fleet, log, sessions, step

# An open session idle longer than this is not counted as working on its story.
ACTIVE = 3600.0


def strays(
    found: list[fleet.Story], archived: list[fleet.Story] | None = None
) -> list[tuple[fleet.Story, fleet.Story]]:
    """Every open story in no feature that a story of a feature split or parked, with that story."""
    held = {fleet.folded(story.key): story for story in found}
    every = {fleet.folded(story.key): story for story in [*found, *(archived or [])]}
    out: dict[tuple[str, int], tuple[fleet.Story, fleet.Story]] = {}
    pairs = [(key, story) for story in every.values() if story.feature is not None for key in fleet.shed(story)]
    for kid in found:
        drafted = [entry.text for entry in log.entries(kid.issue) if entry.prefix == "Drafted:"]
        named = next((text.removeprefix("parked from ") for text in drafted if text.startswith("parked from ")), "")
        if (match := fleet.SHED.fullmatch(named)) is not None:
            pairs.append(
                (fleet.folded(kid.key), every.get(fleet.folded((match.group(1) or kid.repo, int(match.group(2))))))
            )
    for key, parent in pairs:
        kid = held.get(key)
        if parent and parent.feature and kid and kid.feature is None and not kid.closed and fleet.touched(kid):
            out.setdefault(key, (kid, parent))
    return list(out.values())


def circle(start: fleet.Key, blockers: fleet.Blockers) -> list[fleet.Key]:
    """The stories on a loop from `start` back to itself through its blockers, or empty."""
    stack: list[tuple[fleet.Key, list[fleet.Key]]] = [(start, [start])]
    seen: set[fleet.Key] = set()
    while stack:
        node, path = stack.pop()
        for where, number, _ in blockers.get(node) or []:
            if (where, number) == start:
                return [*path, start]
            if (where, number) not in seen:
                seen.add((where, number))
                stack.append(((where, number), [*path, (where, number)]))
    return []


@dataclass(frozen=True)
class Anomaly:
    """Something on the board that no step could have produced, and what puts it right."""

    number: int
    repo: str
    what: str
    fix: str


def find(
    found: list[fleet.Story],
    blockers: fleet.Blockers,
    behind: set[fleet.Key],
    pulses: list,
    missing: list[tuple[str, int, str]] | None = None,
    me: str = "",
    archived: list[fleet.Story] | None = None,
) -> list[Anomaly]:
    """Every disagreement worth a line: a wrong Status, a story off the board or archived, a stall, a cycle."""
    on: dict[tuple[str, str], list[str]] = {}
    # A running session holds its story however long the person has been away.
    held: set[tuple[str, str]] = set()
    for beat in pulses:
        if beat.story == sessions.FREE:
            continue
        if beat.idle < ACTIVE:
            on.setdefault((beat.repo, beat.story), []).append(beat.label)
        if beat.live or beat.idle < ACTIVE:
            held.add((beat.repo, beat.story))
    out: list[Anomaly] = []
    owned = {fleet.folded(story.key) for story in found if fleet.touched(story)}
    for story in found:
        if not fleet.touched(story):
            continue
        # Two typed sources, so they can still disagree: only finished work closes an issue, and
        # only a closed story reaches the last two columns.
        if story.status == columns.DONE and not story.closed:
            out.append(Anomaly(story.number, story.repo, "Done, but the issue is open", "close it or move it back"))
        if story.closed and story.status and story.status not in (columns.DONE, columns.VERIFICATION):
            what = f"closed, but {story.status}"
            # Dropped work has no step to run; finished work closed by hand still has after to reach Done.
            if story.dropped:
                fix = f'captain apply --withdraw {story.repo}#{story.number} --note "<why>"'
            else:
                fix = f"run next {story.number}"
            out.append(Anomaly(story.number, story.repo, what, fix))
        labels = on.get((story.repo, str(story.number))) or []
        mine = not me or not story.assignees or me in story.assignees
        alone = (story.repo, str(story.number)) not in held
        if story.status == columns.IN_PROGRESS and alone and mine and not blockers.get(story.key):
            out.append(Anomaly(story.number, story.repo, "In Progress, no session open", "none"))
        if len(labels) > 1:
            named = " and ".join(labels)
            out.append(Anomaly(story.number, story.repo, f"sessions {named} are both on it", "close one"))
        if story.key in behind:
            out.append(Anomaly(story.number, story.repo, "pull request behind main", f"run next {story.number}"))
        loop = circle(story.key, blockers)
        # One loop is one row, on the first of its own stories, so one command is named once.
        first = min((fleet.folded(key) for key in loop if fleet.folded(key) in owned), default=None)
        if loop and fleet.folded(story.key) == first:
            named = " -> ".join(step.ref_label(where, number, story.repo) for where, number in loop)
            fix = f"captain apply --unblock {'#'.join(map(str, loop[0]))} --by {'#'.join(map(str, loop[1]))}"
            out.append(Anomaly(story.number, story.repo, f"blockers run in a circle: {named}", fix))
    for story, shedder in strays(found, archived):
        name = shedder.feature_name.replace("|", "\\|")
        what = f"split or parked from a story of {name}, but in no feature"
        # A name may hold quotes or shell characters, and a bare number is the repository epic add runs in.
        fix = f"epic add {shedder.feature} {story.repo}#{story.number}"
        out.append(Anomaly(story.number, story.repo, what, fix))
    for repo, number, _ in missing or []:
        out.append(Anomaly(number, repo, "drafted, but not on the board", "captain apply --repair"))
    for story in archived or []:
        # An archived story is off every board read, and only one still in play is missed there.
        if not fleet.touched(story) or story.status == columns.DONE:
            continue
        what = f"archived, and still {story.status or 'without a column'}"
        out.append(Anomaly(story.number, story.repo, what, "captain apply --repair"))
    return out
