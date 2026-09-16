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
from deckhand.step import Refusal, issue_ref, ref_label


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


def feature(settings: Settings, repo: str, text: str, flag: str | None, target: str | None, blocks: str | None) -> int:
    """Park a feature with no stories, in this repository or another, and block a story here on it."""
    if not stub.is_stub(text):
        raise Refusal(f"a parked feature starts with {stub.STUB_HEADING}")
    requirements = stub.read(text)[0]
    if not requirements.strip():
        raise Refusal(f"{stub.STUB_HEADING} has no text")
    if stub.lists_stories(text):
        raise Refusal("a parked feature has no stories yet; use --split")
    target = target or repo
    story = None
    if blocks:
        try:
            story = issue_ref(blocks, repo)
        except ValueError as error:
            raise Refusal(str(error)) from error
        if story[0] != repo:
            raise Refusal("--blocks names a story in this repository")
    first = next(line for line in requirements.splitlines() if line.strip())
    title = (flag or "").strip() or first.strip()
    origin = ref_label(repo, story[1], target) if story else repo
    number, _ = open_parked(settings, repo, target, title, stub.render(requirements, []), origin)
    if story is not None:
        block(repo, story[1], target, number, title)
    return 0
