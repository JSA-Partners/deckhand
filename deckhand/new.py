"""The first step: brainstorm and plan a story into a draft file, then lint it and open the issue.

`context` says where the draft goes, what shape it takes, and which rules the body has to meet, so
the skill never has to carry the contract in prose. It takes the session's starting point too:
nothing, a file holding a request, the request itself, or the number of a stub, whose feature it
prints whole so the session can judge what belongs in this story rather than in a sibling.

`apply` lints the draft, opens the issue or writes the draft into the stub it stands for, boards it
as Draft, and logs `Drafted:`; the draft is the model's working file and the issue the only record
that outlives it. A written stub keeps its number, so its split-time dependencies are its own.

A request bigger than one story takes the other two forms: `--split` opens one stub per story and
records what waits on what, and ends on the line that sends one author agent to each of them; and
`--park` opens a feature stub for a feature nobody is writing yet.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from deckhand import board, fields, gh, issue, lint, log, naming, sections, stub
from deckhand.config import BODY_LIMIT, Settings
from deckhand.step import (
    Refusal,
    block,
    blockers_block,
    draft_line,
    fits_title,
    issue_number,
    read_draft,
    reason,
    resolved_settings,
    step,
    usable,
)

DRAFT = "new.md"
SPLIT = "split.md"
DEPENDS_HEADING = "## Depends on"
SPLIT_NOTE = "If this is more than one story, write the split file instead."
DRAFTED = "Drafted: the story and its plan, from the request."
STUB_DRAFTED = "Drafted: the story and its plan, from the stub."

# One line per rule `lint.lint` enforces, in plain words. A line that says something may be empty is
# a permission; every other line is a rule a body can break, and `tests/test_new.py` pins that.
RULES = [
    "The body holds Story, Scope, Acceptance Criteria, Plan, and Notes, once each and in that order.",
    f"The body is at most {BODY_LIMIT} characters.",
    "Story is one sentence: As a <role>, I want <outcome>, so that <reason>.",
    "Scope has '#### In' bullets and then '#### Out' bullets, and Out is never empty, because Out is "
    "the fence scope creep is measured against.",
    "Every Acceptance Criteria bullet is one Given, When, Then sentence about behaviour, with no CI, "
    "test, or pull request lines, and nothing that belongs in Scope Out.",
    "Plan holds at least a '### Task 1' block.",
    "Notes may be empty.",
]
# The one rule the body cannot break, because it is about the title rather than the text: `title`
# is what enforces it, and the limit is the configured kinds', so the line is written when printed.
TITLE_RULE = (
    "The title is the Story's I want clause unless --title says otherwise, and either way at most "
    "{limit} characters, because the pull request subject is built from it, so keep the clause "
    "short or pass --title."
)


def rules() -> list[str]:
    """Every rule a context prints: the body's, and then the title's, whose limit is computed."""
    return [*RULES, TITLE_RULE.format(limit=naming.title_limit())]


def skeleton() -> str:
    """The empty body a draft starts from: the story's sections, In and Out under Scope."""
    blocks: list[str] = []
    for name in sections.SECTIONS:
        blocks.append(f"### {name}")
        if name == "Scope":
            blocks += ["#### In", "#### Out"]
    return "\n\n".join(blocks)


# --- context ----------------------------------------------------------------


def _draft_name(number: int) -> str:
    """The draft file for the story stub `number` becomes, inside the repository's cache."""
    return f"{number}-story.md"


def _split_name(number: int) -> str:
    """The split file for the feature `number` holds, inside the repository's cache."""
    return f"{number}-split.md"


def _split_block(name: str) -> None:
    """Where the split file goes and the shape it takes; the feature path starts here."""
    print(draft_line("Split file", name))
    print()
    print(stub.split_skeleton())


def _rules() -> int:
    """The contract the body has to meet; every starting point ends on it."""
    print("Rules:")
    for rule in rules():
        print(f"- {rule}")
    return 0


def _tail(name: str, split: str | None = None) -> int:
    """Where the draft goes, the shape it takes, and the rules it has to meet.

    `split` names the split file to offer after the rules, for a starting point that may yet turn
    out to be a whole feature; a stub is already one story of one, so it is offered nothing.
    """
    print(draft_line("Draft", name))
    print()
    print(skeleton())
    print()
    _rules()
    if split is not None:
        print()
        print(SPLIT_NOTE)
        _split_block(split)
    return 0


def _request_context(source: str) -> int:
    """The starting point handed over: the text of a file, or the request typed after the command.

    A source that is not a file is the request itself, because what a person types there is a
    request far more often than it is a path; either way it goes under the heading a stub uses.
    """
    path = Path(source)
    try:
        text = path.read_text(encoding="utf-8-sig").strip("\n") if path.is_file() else source
    except (OSError, UnicodeDecodeError) as error:
        print(f"Requirements: unavailable ({reason(error)})")
    else:
        if not stub.is_stub(text):  # a request already under the heading keeps the one it has
            print(stub.STUB_HEADING)
            print()
        print(text)
    print()
    return _tail(DRAFT, SPLIT)


def _state(repo: str, number: int) -> str:
    """A sibling's state: `stub` while it is still one, else the state GitHub holds."""
    try:
        state, body = issue.sibling(repo, number)
    except Exception:  # one sibling that will not load costs its own line, not the list
        return "unavailable"
    return "stub" if stub.is_stub(body) else (state or "unknown").lower()


def _entry_line(repo: str, position: int, entry: stub.Entry, own: int) -> str:
    """One story of the feature, with where it has got to; the one being written says so.

    The sentence rides along with the title: the boundary between two stories is what the session
    has to judge, and it is in the sentences, not the titles.
    """
    scope = f"{entry.title} | {entry.sentence}"
    if entry.number is None:
        return f"{position}. {scope} (not opened)"
    state = "this one" if entry.number == own else _state(repo, entry.number)
    return f"{position}. #{entry.number} {scope} ({state})"


def _parked_context(number: int, requirements: str) -> int:
    """A feature nobody has split yet: its requirements, and the one command that splits them."""
    print(f"## Parked feature #{number}")
    print()
    print(requirements)
    print()
    _split_block(_split_name(number))
    print()
    print(
        "This is a parked feature: settle its requirements, then write the split file and run "
        f"new apply --split <file> --from {number}."
    )
    return 0


def _issue_context(number: int) -> int:
    """The stub's whole feature, or the one line that says this number is not a stub to write."""
    try:
        repo = gh.repo_slug()
        story = issue.view(repo, number)
    except Exception as error:
        failure = error  # `error` is unbound once the except block ends, and the block reads it
        block(f"## Stub #{number}", lambda: usable(failure))
        print()
        return _tail(_draft_name(number))
    if not stub.is_stub(story.body):
        print(f"#{number} is already a story; run /deckhand:next {number}")
        print()
        return _rules()
    requirements, entries = stub.read(story.body)
    if not entries:
        return _parked_context(number, requirements)
    print(f"## Stub #{number}")
    print()
    print(requirements)
    print()
    block(stub.STORIES_HEADING, lambda: [_entry_line(repo, i, e, number) for i, e in enumerate(entries, start=1)])
    print()
    block(DEPENDS_HEADING, lambda: blockers_block(repo, number))
    print()
    return _tail(_draft_name(number))


def context(args: argparse.Namespace) -> int:
    """Print the starting point, then where the draft goes, its shape, and the rules it must meet."""
    source = (args.source or "").strip()
    if not source:  # the skill passes its argument quoted, so an empty one arrives as a blank word
        return _tail(DRAFT, SPLIT)
    if source.isascii() and source.isdigit():
        return _issue_context(int(source))
    return _request_context(source)


# --- apply ------------------------------------------------------------------


def title(flag: str | None, body: str) -> str:
    """The title `naming` takes from `--title` or the Story, refused here when it cannot make one."""
    try:
        return naming.title(flag, body)
    except ValueError as error:
        raise Refusal(str(error)) from error


def board_draft(settings: Settings, repo: str, number: int, url: str, note: str | None) -> None:
    """Put the issue on the board as Draft, then log that it was written when `note` is given.

    Shared with the amend step, which boards the story it splits off the same way; every write prints
    as it lands, so the printed lines are the record of how far the story got.
    """
    board.add(settings, url)
    print("Added to the board", flush=True)
    print(fields.set_field(settings, repo, number, "Status", "Draft"), flush=True)
    if note is not None:
        issue.comment(repo, number, log.checked(note))
        print(f"Logged {note.split(':', 1)[0]}", flush=True)


def _write_stub(repo: str, number: int, draft: str, flag: str | None) -> int:
    """Rewrite the stub as the story it stands for; its number and dependencies are untouched."""
    story = issue.view(repo, number)
    if not stub.is_stub(story.body):
        raise Refusal(f"#{number} is already a story; run /deckhand:next {number}")
    if not stub.read(story.body)[1]:
        raise Refusal(f"#{number} is a parked feature; run /deckhand:new {number} to split it")
    subject = fits_title((flag or "").strip())
    body = lint.checked(draft)
    settings = resolved_settings()
    issue.update_body(repo, number, body)
    print(f"Written #{number} {story.url}", flush=True)
    if subject:  # the stub's own title stands unless the session says otherwise
        issue.set_title(repo, number, subject)
        print(f"Title: {subject}", flush=True)
    board_draft(settings, repo, number, story.url, STUB_DRAFTED)
    return 0


def _parked(repo: str, number: int) -> None:
    """Refuse anything but a feature nobody has split yet; this runs before the first write.

    A split closes the feature it came from, so a closed one has been split already and running the
    same file again would open the whole set a second time.
    """
    state, body = issue.sibling(repo, number)
    if not stub.is_stub(body) or stub.read(body)[1]:
        raise Refusal(f"#{number} is not a parked feature")
    if state.lower() != "open":
        raise Refusal(f"#{number} was already split")


def _split(repo: str, text: str, parked: int | None) -> int:
    """Open one stub per story of a feature, number them all, then record what waits on what.

    Every write prints as it lands and nothing is retried, so a failure part way through leaves the
    printed lines as the record of what was opened.
    """
    try:
        requirements, entries = stub.parse_split(text)
    except ValueError as error:
        raise Refusal(f"split file {error}") from error
    if parked is not None:
        _parked(repo, parked)
    settings = resolved_settings()
    # Every stub carries the whole feature, so the first pass writes it unnumbered: no story can
    # name its siblings' numbers until every issue exists.
    provisional = stub.render(requirements, entries)
    numbers: list[int] = []
    urls: list[str] = []
    for entry in entries:
        number, url = issue.create(repo, entry.title, provisional)
        print(f"Created #{number} {entry.title}", flush=True)
        numbers.append(number)
        urls.append(url)
    body = stub.render(requirements, [replace(e, number=n) for e, n in zip(entries, numbers, strict=True)])
    for number, url in zip(numbers, urls, strict=True):
        issue.update_body(repo, number, body)
        print(f"Numbered #{number}", flush=True)
        board_draft(settings, repo, number, url, None)  # a stub is not drafted yet; its author logs that
    for entry, number in zip(entries, numbers, strict=True):
        for position in entry.after:
            blocker = numbers[position - 1]
            try:
                issue.add_dependency(repo, number, blocked_by=blocker)
            except Exception as error:  # the edge is the one thing the printed record cannot show
                raise Refusal(f"#{number} blocked by #{blocker} failed: {reason(error)}; add it by hand") from error
            print(f"#{number} blocked by #{blocker}", flush=True)
    if parked is not None:
        issue.comment(repo, parked, f"Split into {', '.join(f'#{number}' for number in numbers)}.")
        issue.close(repo, parked)
        print(f"Closed #{parked}", flush=True)
    # An agent gets no plugin-root substitution, so the launcher it is to run travels in the line.
    stubs = " ".join(f"#{number}" for number in numbers)
    print(f"Dispatch deckhand:author for each of {stubs} with {Path(sys.argv[0]).resolve()}")
    return 0


def _park(repo: str, text: str, flag: str | None) -> int:
    """Open a feature stub with no stories: a feature the session settled but nobody is splitting."""
    if not stub.is_stub(text):
        raise Refusal(f"a parked feature starts with {stub.STUB_HEADING}")
    requirements = stub.read(text)[0]
    if not requirements.strip():
        raise Refusal(f"{stub.STUB_HEADING} has no text")
    if stub.lists_stories(text):
        raise Refusal("a parked feature has no stories yet; use --split")
    first = next(line for line in requirements.splitlines() if line.strip())
    number, url = issue.create(repo, (flag or "").strip() or first.strip(), stub.render(requirements, []))
    print(f"Parked #{number} {url}")
    return 0


def _configure_context(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "source",
        nargs="?",
        help="an issue number when all digits; else a file when one exists, else the request (./57 names a file)",
    )


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", type=Path, help="the draft body file")
    form = parser.add_mutually_exclusive_group()
    form.add_argument("--stub", type=issue_number, metavar="N", help="write the draft into stub N")
    form.add_argument("--split", action="store_true", help="the file is a split file: one stub per story")
    form.add_argument("--park", action="store_true", help="the file is a feature to stub and leave for later")
    parser.add_argument(
        "--from",
        dest="parked",
        type=issue_number,
        metavar="N",
        help="with --split, the parked feature these stories came from; it is closed at the end",
    )
    parser.add_argument("--title", help="the issue title; the default is the Story's I want clause")
    # Only the parser that read these arguments knows the usage line to print a usage error with,
    # and argparse cannot say that one option is allowed only alongside another.
    parser.set_defaults(usage=parser)


@step("new", _configure, issue_bound=False, configure_context=_configure_context)
def apply(args: argparse.Namespace) -> int:
    """Open a story or a stub from the drafted body, split a feature, or park one for later."""
    if args.parked is not None and not args.split:
        args.usage.error("--from is only for --split")
    if args.split and args.title is not None:
        args.usage.error("--title is not for --split; every story takes its title from the file")
    draft = read_draft(args.file)
    if args.stub is not None:
        return _write_stub(gh.repo_slug(), args.stub, draft, args.title)
    if args.split:
        return _split(gh.repo_slug(), draft, args.parked)
    if args.park:
        return _park(gh.repo_slug(), draft, args.title)
    body = lint.checked(draft)
    subject = title(args.title, body)
    settings = resolved_settings()
    repo = gh.repo_slug()
    number, url = issue.create(repo, subject, body)
    print(f"Created #{number} {url}", flush=True)
    board_draft(settings, repo, number, url, DRAFTED)
    return 0
