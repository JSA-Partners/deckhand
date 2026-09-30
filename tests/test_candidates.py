"""What to open next: the unstarted stories, the step each needs, what it waits on, and the files it names."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from deckhand import candidates, columns, fleet

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "acme/widgets"
PARKED = "## Requirements\nSign in with Clerk.\n"
DRAFTED = PARKED + "\n## Stories\n1. Wire the session | A person stays signed in.\n"


def _nodes() -> list[dict]:
    data = json.loads((FIXTURES / "captain-items.json").read_text(encoding="utf-8"))
    return data["data"]["organization"]["projectV2"]["items"]["nodes"]


def _story(number: int, status: str, body: str = "", closed: bool = False) -> fleet.Story:
    story = {story.number: story for story in fleet.stories(_nodes())}[253]
    return dataclasses.replace(
        story,
        number=number,
        status=status,
        closed=closed,
        title=f"Story {number}",
        issue=dataclasses.replace(story.issue, number=number, body=body),
    )


def _plan(*paths: str) -> str:
    named = " and ".join(f"`{path}`" for path in paths)
    return f"### Story\nA person signs in.\n\n### Plan\nEdit {named}.\n"


def _fleet(*found: fleet.Story, blockers: fleet.Blockers | None = None) -> fleet.Fleet:
    return fleet.Fleet(stories=list(found), blockers=blockers or {}, behind=set(), missing=[])


def _row(lines: list[str], number: int) -> list[str]:
    (line,) = [line for line in lines if line.startswith(f"| {number} |")]
    return [cell.strip() for cell in line.strip("|").split("|")]


NEXT = 5
WAITS = 6
NAMED = 7
FILES = 8


def test_each_unstarted_column_names_the_step_it_needs():
    read = _fleet(
        _story(1, columns.DRAFT, PARKED),
        _story(2, columns.DRAFT, DRAFTED),
        _story(3, columns.REFINEMENT, _plan("a/b.go")),
        _story(4, columns.READY, _plan("a/b.go")),
        _story(5, columns.BACKLOG, _plan("a/b.go")),
        _story(6, columns.BACKLOG, _plan("a/b.go")),
        blockers={(REPO, 6): [(REPO, 5, "Story 5")]},
    )
    lines = candidates.rows(read, REPO)
    steps = {number: _row(lines, number)[NEXT] for number in range(1, 7)}
    assert steps == {1: "settle", 2: "write", 3: "review", 4: "board", 5: "start", 6: "wait"}


def test_only_unstarted_open_deckhand_stories_are_rows():
    foreign = _story(4, columns.BACKLOG)
    foreign = dataclasses.replace(foreign, issue=dataclasses.replace(foreign.issue, labels=()))
    read = _fleet(
        _story(1, columns.BACKLOG),
        _story(2, columns.IN_PROGRESS),
        _story(3, columns.BACKLOG, closed=True),
        foreign,
        _story(5, columns.IN_REVIEW),
    )
    listed = [line for line in candidates.rows(read, REPO) if line.startswith("| ") and line[2].isdigit()]
    assert [line.split("|")[1].strip() for line in listed] == ["1"]


def test_a_blocker_edge_is_what_it_waits_on_and_not_a_named_reference():
    read = _fleet(
        _story(1, columns.BACKLOG, "Builds on #2 and acme/gadgets#9.\n\n" + _plan("a/b.go")),
        _story(2, columns.BACKLOG),
        blockers={(REPO, 1): [(REPO, 2, "Story 2"), ("acme/gadgets", 9, "Deploy")]},
    )
    row = _row(candidates.rows(read, REPO), 1)
    assert row[WAITS] == "#2, acme/gadgets#9"
    assert row[NAMED] == "-"


def test_an_edge_the_other_way_is_not_named_either():
    read = _fleet(
        _story(1, columns.BACKLOG, "Unblocks #2."),
        _story(2, columns.BACKLOG),
        blockers={(REPO, 2): [(REPO, 1, "Story 1")]},
    )
    assert _row(candidates.rows(read, REPO), 1)[NAMED] == "-"


def test_a_board_story_named_without_an_edge_is_listed():
    read = _fleet(_story(1, columns.READY, "Shares the session with #2 and #1."), _story(2, columns.IN_PROGRESS))
    assert _row(candidates.rows(read, REPO), 1)[NAMED] == "#2"


def test_a_named_story_that_is_closed_or_off_the_board_is_not_listed():
    read = _fleet(
        _story(1, columns.READY, "After #2 and #3."),
        _story(2, columns.DONE, closed=True),
    )
    assert _row(candidates.rows(read, REPO), 1)[NAMED] == "-"


def test_the_files_are_a_count_of_the_distinct_paths_the_plan_names():
    body = _plan("internal/auth/clerk.go:12", "internal/auth/clerk.go:40-44", "internal/auth/session.go")
    body = "Mentions `docs/outside.md` above the plan.\n\n" + body
    read = _fleet(_story(1, columns.BACKLOG, body))
    assert _row(candidates.rows(read, REPO), 1)[FILES] == "2"


def test_an_overlap_names_both_stories_and_the_common_files():
    read = _fleet(
        _story(1, columns.BACKLOG, _plan("internal/auth/clerk.go", "internal/auth/session.go")),
        _story(2, columns.READY, _plan("internal/auth/clerk.go:9", "cmd/api/main.go")),
    )
    assert candidates.rows(read, REPO)[-1] == "Overlap: #1 and #2: internal/auth/clerk.go"


def test_an_overlap_shows_three_paths_then_a_count():
    paths = ("a/1.go", "a/2.go", "a/3.go", "a/4.go", "a/5.go")
    read = _fleet(_story(1, columns.BACKLOG, _plan(*paths)), _story(2, columns.BACKLOG, _plan(*paths)))
    assert candidates.rows(read, REPO)[-1] == "Overlap: #1 and #2: a/1.go, a/2.go, a/3.go and 2 more"


def test_no_common_file_says_none():
    read = _fleet(_story(1, columns.BACKLOG, _plan("a/1.go")), _story(2, columns.BACKLOG, _plan("a/2.go")))
    assert candidates.rows(read, REPO)[-1] == "Overlap: none"


def test_a_story_in_progress_counts_for_overlap_but_is_not_a_row():
    read = _fleet(_story(1, columns.BACKLOG, _plan("a/1.go")), _story(2, columns.IN_PROGRESS, _plan("a/1.go")))
    lines = candidates.rows(read, REPO)
    assert not any(line.startswith("| 2 |") for line in lines)
    assert lines[-1] == "Overlap: #1 and #2: a/1.go"


def test_two_stories_already_in_progress_are_not_an_overlap_to_plan_around():
    read = _fleet(
        _story(1, columns.BACKLOG, _plan("a/9.go")),
        _story(2, columns.IN_PROGRESS, _plan("a/1.go")),
        _story(3, columns.IN_PROGRESS, _plan("a/1.go")),
    )
    assert candidates.rows(read, REPO)[-1] == "Overlap: none"


def test_the_same_path_in_two_repositories_is_not_an_overlap():
    other = dataclasses.replace(_story(2, columns.BACKLOG, _plan("a/1.go")), repo="acme/gadgets")
    read = _fleet(_story(1, columns.BACKLOG, _plan("a/1.go")), other)
    assert candidates.rows(read, REPO)[-1] == "Overlap: none"


def test_nothing_unstarted_says_so():
    assert candidates.rows(_fleet(_story(1, columns.IN_PROGRESS)), REPO) == ["  nothing waiting to start"]


def test_a_pair_ordered_through_a_chain_is_not_an_overlap():
    read = _fleet(
        _story(1, columns.BACKLOG, _plan("a/1.go")),
        _story(2, columns.BACKLOG),
        _story(3, columns.BACKLOG, _plan("a/1.go")),
        blockers={(REPO, 3): [(REPO, 2, "Story 2")], (REPO, 2): [(REPO, 1, "Story 1")]},
    )
    assert candidates.rows(read, REPO)[-1] == "Overlap: none"


def test_a_file_in_three_plans_is_shared_and_not_an_overlap():
    read = _fleet(
        _story(1, columns.BACKLOG, _plan("a/1.go", "a/2.go")),
        _story(2, columns.BACKLOG, _plan("a/1.go", "a/2.go")),
        _story(3, columns.IN_PROGRESS, _plan("a/1.go")),
    )
    assert candidates.rows(read, REPO)[-2:] == ["Shared: a/1.go (3 plans)", "Overlap: #1 and #2: a/2.go"]


def test_shared_names_the_most_named_five_then_a_count():
    paths = [f"a/{n}.go" for n in range(1, 8)]
    read = _fleet(
        _story(1, columns.BACKLOG, _plan(*paths)),
        _story(2, columns.BACKLOG, _plan(*paths)),
        _story(3, columns.BACKLOG, _plan(*paths)),
        _story(4, columns.BACKLOG, _plan("a/7.go")),
    )
    shared = "Shared: a/7.go (4 plans), a/1.go (3 plans), a/2.go (3 plans), a/3.go (3 plans), a/4.go (3 plans)"
    assert candidates.rows(read, REPO)[-2:] == [f"{shared} and 2 more", "Overlap: none"]


def test_no_shared_file_leaves_the_line_out():
    read = _fleet(_story(1, columns.BACKLOG, _plan("a/1.go")), _story(2, columns.BACKLOG, _plan("a/1.go")))
    assert not any(line.startswith("Shared:") for line in candidates.rows(read, REPO))


def test_a_story_named_only_as_out_of_scope_is_not_listed():
    scope = "### Scope\n#### In\n- Sessions, as #3 left them.\n#### Out\n- Webhooks, which are #2 and\n  #4.\n"
    read = _fleet(
        _story(1, columns.READY, scope + _plan("a/1.go")),
        _story(2, columns.BACKLOG),
        _story(3, columns.BACKLOG),
        _story(4, columns.BACKLOG),
    )
    assert _row(candidates.rows(read, REPO), 1)[NAMED] == "#3"
