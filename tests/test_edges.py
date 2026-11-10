"""The edges block: every blocker edge of the open stories, both ways, for a board audit."""

from __future__ import annotations

from dataclasses import replace

from deckhand import edges, fleet, issue

REPO = "acme/widgets"


def _story(number: int, status: str, *held: tuple[int, str], closed: bool = False) -> fleet.Story:
    body = issue.Issue(number, f"T{number}", "", "", "CLOSED" if closed else "OPEN", [], ("deckhand",))
    return fleet.Story(
        number=number,
        repo=REPO,
        title=f"T{number}",
        status=status,
        points=None,
        closed=closed,
        item=f"I_{number}",
        issue=body,
        edges=tuple((REPO, n, state) for n, state in held),
    )


def _read(*stories: fleet.Story) -> fleet.Fleet:
    return fleet.Fleet(stories=list(stories), blockers={}, missing=[])


def test_rows_show_both_directions_with_state_and_column():
    read = _read(
        _story(26, "Backlog", (376, "open"), (361, "closed"), (348, "dropped")),
        _story(376, "In Progress"),
        _story(361, "Done", closed=True),
    )

    assert edges.rows(read) == [
        "| # | Repo | Waits on | Blocks |",
        "| --- | --- | --- | --- |",
        "| 26 | widgets | #376 open, In Progress; #361 closed, Done; #348 dropped | - |",
        "| 376 | widgets | - | #26 open, Backlog |",
    ]


def test_a_story_with_no_edge_either_way_has_no_row():
    assert edges.rows(_read(_story(5, "Backlog"))) == ["  no blocker edges"]


def test_a_closed_story_has_no_row_of_its_own():
    read = _read(_story(26, "Backlog", (361, "closed")), replace(_story(361, "Done", closed=True)))

    assert [line.split(" | ")[0] for line in edges.rows(read)[2:]] == ["| 26"]
