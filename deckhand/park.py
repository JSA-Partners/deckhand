"""A parked feature: a draft opened in any repository of the project, boarded as Draft, and logged.

The park and the split both open drafts in repositories the session is not in, and this is the one
place that does it: the issue opens there with the body given, the shared project takes it by URL,
and the log says where it came from. Nothing here reads that repository's code, and nothing here
writes a plan: the repository's own session does both when it runs new on the draft. `board_draft`
lives here because every draft and story boards the same way, from new, amend, and the park alike.
"""

from __future__ import annotations

from deckhand import board, columns, draft, edges, epic, fields, fleet, gh, issue, log, related
from deckhand.config import Settings
from deckhand.step import Refusal, fits_title, issue_ref, reason, ref_label

PARKED_HEADING = "## Parked feature"


def feature_of(stories: list[fleet.Story], came: tuple[str, int] | None) -> str:
    """The Feature the story `came` names is in, or empty for no story, one in no feature, or one off the board."""
    if came is None:
        return ""
    wanted = fleet.folded(came)
    return next((story.feature_name for story in stories if fleet.folded(story.key) == wanted), "")


def board_draft(
    settings: Settings,
    repo: str,
    number: int,
    url: str,
    note: str | None,
    status: str = columns.DRAFT,
    feature: str = "",
    *,
    resolved: fields.Resolved,
) -> None:
    """Put the issue on the board in `status` and `feature`, then log that it was written when `note` is given.

    Every write prints as it lands, so the printed lines are the record of how far the story got.
    `resolved` was read before the caller's first write, so no field read can strand the story here.
    """
    item = board.add(settings, url) or gh.item_id(settings, repo, number)
    print("Added to the board", flush=True)
    if item is None:
        raise fields.NotOnBoard(f"issue #{number} did not reach project {settings.owner}/{settings.project}")
    print(fields.write(resolved, item, "Status", status), flush=True)
    # The log first: a Feature write that fails still leaves the entry the stray check reads.
    if note is not None:
        issue.comment(repo, number, log.checked(note))
        print(f"Logged {note.split(':', 1)[0]}", flush=True)
    if feature:
        try:
            print(fields.write(resolved, item, board.FEATURE, feature), flush=True)
        except gh.GhError as error:
            option = fields.option(resolved, board.FEATURE, feature)
            raise Refusal(f"Feature not set; epic add {option} {repo}#{number}: {reason(error)}") from error


def fields_for(settings: Settings, feature: str) -> fields.Resolved:
    """Every field a new story is boarded with, read and checked before anything is written."""
    resolved = fields.resolve(settings, ("Status", board.FEATURE) if feature else ("Status",))
    if feature:
        fields.option(resolved, board.FEATURE, feature)
    return resolved


def chosen(settings: Settings, named: str | None, inherited: str) -> str:
    """The Feature a new story goes into: the one named, else the one it inherits, refused when neither."""
    if named:
        return epic.option_name(settings, named)
    if inherited:
        return inherited
    names = ", ".join(epic.option_names(settings)) or "none yet; open one with epic open"
    raise Refusal(f"a story needs a feature: pass --feature <name>; the features are {names}")


def open_parked(
    settings: Settings,
    here: str,
    target: str,
    title: str,
    body: str,
    origin: str,
    stories: list[fleet.Story],
    came: tuple[str, int] | None = None,
    feature: str = "",
    resolved: fields.Resolved | None = None,
) -> tuple[int, str]:
    """Open `body` as a draft titled `title` in `target`, board it as Draft, and log where it came from.

    `stories` is the board as read before the first write, so the stories this one resembles are
    named ahead of it in any repository, and `came`, the story it was shed from, lends it a feature.
    """
    gh.split_repo(target)
    # Work shed from a story belongs to that story's feature until someone moves it with epic add.
    taken = feature or feature_of(stories, came)
    resolved = resolved or fields_for(settings, taken)
    for line in related.similar_lines([story for story in stories if not story.archived], title, here):
        print(line, flush=True)
    number, url = issue.create(target, title, body)
    print(f"Parked {ref_label(target, number, here)} {url}", flush=True)
    board_draft(settings, target, number, url, f"Drafted: parked from {origin}", feature=taken, resolved=resolved)
    return number, url


def block(here: str, story: int, target: str, number: int, title: str) -> None:
    """Record that `story` in `here` waits on `target#number`: the story's log first, then the dependency."""
    issue.comment(here, story, log.checked(f"Parked: {ref_label(target, number, here)} {title}"))
    print("Logged Parked", flush=True)
    issue.add_dependency(here, story, (target, number))
    print(f"#{story} blocked by {ref_label(target, number, here)}", flush=True)


def unsplit(repo: str, number: int) -> str:
    """The feature's URL; refuse anything but a feature nobody has split yet, before the first write.

    A split writes the numbered draft body onto the feature it came from, and that body lists its
    stories, so a feature that lists them has been split already and running the same file again
    would open the rest a second time.
    """
    story = issue.view(repo, number)
    if not draft.is_draft(story.body) or draft.read(story.body)[1]:
        raise Refusal(f"#{number} is not a parked feature")
    return story.url


def _ref(value: str, repo: str) -> tuple[str, int]:
    """One issue reference, with the form named when it will not parse."""
    try:
        return issue_ref(value, repo)
    except ValueError as error:
        raise Refusal(str(error)) from error


def as_stub(text: str) -> str:
    """`text` as a draft body: a file that carries no requirements heading is the requirements itself.

    `new context` prints a parked feature under its own `## Parked feature #N`, which is the heading
    a session copies, so that one is replaced rather than kept inside the requirements.
    """
    if draft.is_draft(text):
        return text
    lines = text.lstrip("\n").splitlines()
    if lines and lines[0].strip().startswith(PARKED_HEADING):
        lines = lines[1:]
    return "\n".join([draft.REQUIREMENTS_HEADING, "", *lines]).strip("\n") + "\n"


def feature(
    settings: Settings,
    repo: str,
    text: str,
    flag: str | None,
    target: str | None,
    blocks: str | None,
    came_from: int | None = None,
    after: str | None = None,
    named: str | None = None,
) -> int:
    """Park a feature with no stories, in this repository or another, saying where it came from.

    `blocks` is a story here that waits on the feature and names its origin. `came_from` names the
    origin alone, for a review that parked a finding nobody waits on. `after` is a story the feature
    itself waits on, for a split that parks a chain of them.
    """
    text = as_stub(text)
    requirements = draft.read(text)[0]
    if not requirements.strip():
        raise Refusal(f"{draft.REQUIREMENTS_HEADING} has no text")
    if draft.lists_stories(text):
        raise Refusal("a parked feature has no stories yet; use --split")
    target = target or repo
    story = _ref(blocks, repo) if blocks else None
    if story is not None and story[0] != repo:
        raise Refusal("--blocks names a story in this repository")
    waits_on = _ref(after, repo) if after else None
    if story is not None and waits_on is not None:
        # The story waits on the park and the park on `after`, so the story comes to wait on `after`.
        edges.refuse_loop(repo, story, waits_on)
    first = next(line for line in requirements.splitlines() if line.strip())
    # A notes file opens with a sentence, not a name; the subject limit is what tells the two apart.
    title = fits_title((flag or "").strip() or first.strip())
    came = story[1] if story is not None else came_from
    origin = ref_label(repo, came, target) if came is not None else repo
    # Read before the first write, so a board that fails refuses the park rather than leaving it half done.
    # Archived too, so a story the project archived still lends the park its feature.
    stories = fleet.load(settings, archived=True)
    body = draft.render(requirements, [])
    parent = (repo, came) if came is not None else None
    feature = chosen(settings, named, feature_of(stories, parent))
    number, _ = open_parked(settings, repo, target, title, body, origin, stories, parent, feature)
    if story is not None:
        block(repo, story[1], target, number, title)
    if waits_on is not None:
        issue.add_dependency(target, number, blocked_by=waits_on)
        print(f"{ref_label(target, number, repo)} blocked by {ref_label(*waits_on, repo)}", flush=True)
    return 0


def wire(here: str, entries: list[draft.Entry], numbers: list[int]) -> None:
    """Record what each story of a split waits on, in the order the file gave them.

    Every write prints as it lands, and a failed edge is the one thing the printed record cannot
    show, so it refuses rather than carrying on quietly.
    """
    for entry, number in zip(entries, numbers, strict=True):
        home = entry.repo or here
        for position in entry.after:
            blocker = (entries[position - 1].repo or here, numbers[position - 1])
            blocked, by = ref_label(home, number, here), ref_label(*blocker, here)
            try:
                issue.add_dependency(home, number, blocked_by=blocker)
            except Exception as error:
                raise Refusal(f"{blocked} blocked by {by} failed: {reason(error)}; add it by hand") from error
            print(f"{blocked} blocked by {by}", flush=True)


def inherit(here: str, feature: int, siblings: list[tuple[str, int]]) -> None:
    """Give every issue waiting on `feature` an edge to each story it became besides the first.

    The feature is the first story now, so what waited on it still waits on it; the rest of what it
    became has to be waited on too, or the work is released before it is done.
    """
    for where, number, _ in issue.blocking(here, feature):
        for repo, sibling in siblings:
            issue.add_dependency(where, number, blocked_by=(repo, sibling))
            print(f"{ref_label(where, number, here)} blocked by {ref_label(repo, sibling, here)}", flush=True)
