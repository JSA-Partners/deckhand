"""A parked feature: a stub opened in any repository of the project, boarded as Draft, and logged.

The park and the split both open stubs in repositories the session is not in, and this is the one
place that does it: the issue opens there with the body given, the shared project takes it by URL,
and the log says where it came from. Nothing here reads that repository's code, and nothing here
writes a plan: the repository's own session does both when it runs new on the stub. `board_draft`
lives here because every stub and story boards the same way, from new, amend, and the park alike.
"""

from __future__ import annotations

from deckhand import board, fields, gh, issue, log, stub
from deckhand.config import Settings
from deckhand.step import Refusal, fits_title, issue_ref, reason, ref_label

PARKED_HEADING = "## Parked feature"


def board_draft(settings: Settings, repo: str, number: int, url: str, note: str | None) -> None:
    """Put the issue on the board as Draft, then log that it was written when `note` is given.

    Every write prints as it lands, so the printed lines are the record of how far the story got.
    """
    board.add(settings, url)
    print("Added to the board", flush=True)
    print(fields.set_field(settings, repo, number, "Status", "Draft"), flush=True)
    if note is not None:
        issue.comment(repo, number, log.checked(note))
        print(f"Logged {note.split(':', 1)[0]}", flush=True)


def open_parked(settings: Settings, here: str, target: str, title: str, body: str, origin: str) -> tuple[int, str]:
    """Open `body` as a stub titled `title` in `target`, board it as Draft, and log where it came from."""
    gh.split_repo(target)
    number, url = issue.create(target, title, body)
    print(f"Parked {ref_label(target, number, here)} {url}", flush=True)
    board_draft(settings, target, number, url, f"Drafted: parked from {origin}")
    return number, url


def block(here: str, story: int, target: str, number: int, title: str) -> None:
    """Record that `story` in `here` waits on `target#number`: the story's log first, then the dependency."""
    issue.comment(here, story, log.checked(f"Parked: {ref_label(target, number, here)} {title}"))
    print("Logged Parked", flush=True)
    issue.add_dependency(here, story, (target, number))
    print(f"#{story} blocked by {ref_label(target, number, here)}", flush=True)


def unsplit(repo: str, number: int) -> str:
    """The feature's URL; refuse anything but a feature nobody has split yet, before the first write.

    A split writes the numbered stub body onto the feature it came from, and that body lists its
    stories, so a feature that lists them has been split already and running the same file again
    would open the rest a second time.
    """
    story = issue.view(repo, number)
    if not stub.is_stub(story.body) or stub.read(story.body)[1]:
        raise Refusal(f"#{number} is not a parked feature")
    return story.url


def _ref(value: str, repo: str) -> tuple[str, int]:
    """One issue reference, with the form named when it will not parse."""
    try:
        return issue_ref(value, repo)
    except ValueError as error:
        raise Refusal(str(error)) from error


def as_stub(text: str) -> str:
    """`text` as a stub body: a file that carries no requirements heading is the requirements itself.

    `new context` prints a parked feature under its own `## Parked feature #N`, which is the heading
    a session copies, so that one is replaced rather than kept inside the requirements.
    """
    if stub.is_stub(text):
        return text
    lines = text.lstrip("\n").splitlines()
    if lines and lines[0].strip().startswith(PARKED_HEADING):
        lines = lines[1:]
    return "\n".join([stub.STUB_HEADING, "", *lines]).strip("\n") + "\n"


def feature(
    settings: Settings,
    repo: str,
    text: str,
    flag: str | None,
    target: str | None,
    blocks: str | None,
    came_from: int | None = None,
    after: str | None = None,
) -> int:
    """Park a feature with no stories, in this repository or another, saying where it came from.

    `blocks` is a story here that waits on the feature and names its origin. `came_from` names the
    origin alone, for a review that parked a finding nobody waits on. `after` is a story the feature
    itself waits on, for a split that parks a chain of them.
    """
    text = as_stub(text)
    requirements = stub.read(text)[0]
    if not requirements.strip():
        raise Refusal(f"{stub.STUB_HEADING} has no text")
    if stub.lists_stories(text):
        raise Refusal("a parked feature has no stories yet; use --split")
    target = target or repo
    story = _ref(blocks, repo) if blocks else None
    if story is not None and story[0] != repo:
        raise Refusal("--blocks names a story in this repository")
    waits_on = _ref(after, repo) if after else None
    first = next(line for line in requirements.splitlines() if line.strip())
    # A notes file opens with a sentence, not a name; the subject limit is what tells the two apart.
    title = fits_title((flag or "").strip() or first.strip())
    came = story[1] if story is not None else came_from
    origin = ref_label(repo, came, target) if came is not None else repo
    number, _ = open_parked(settings, repo, target, title, stub.render(requirements, []), origin)
    if story is not None:
        block(repo, story[1], target, number, title)
    if waits_on is not None:
        issue.add_dependency(target, number, blocked_by=waits_on)
        print(f"{ref_label(target, number, repo)} blocked by {ref_label(*waits_on, repo)}", flush=True)
    return 0


def wire(here: str, entries: list[stub.Entry], numbers: list[int]) -> None:
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
