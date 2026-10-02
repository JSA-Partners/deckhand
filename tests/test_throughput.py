"""How many stories finish a week, and how many weeks what is left of an epic will take."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta

from deckhand import draft, fleet, issue, throughput

REPO = "acme/widgets"
EPIC = (REPO, 300)
TODAY = date(2026, 10, 2)
DRAFT = "## Requirements\n\nWhat it needs.\n"


def _at(hours: float) -> str:
    return (datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(days: int) -> str:
    return f"{(TODAY - timedelta(days=days)).isoformat()}T12:00:00Z"


def _story(
    number: int,
    closed: bool,
    *log: tuple[str, str, str],
    repo: str = REPO,
    labels: tuple[str, ...] = ("deckhand",),
    parent: tuple[str, int] | None = None,
    body: str = "",
    closed_at: str = "",
) -> fleet.Story:
    comments = [issue.Comment(author="claude", body=f"{prefix} {text}", created_at=at) for prefix, at, text in log]
    held = issue.Issue(
        number=number,
        title="T",
        body=body,
        url="",
        state="CLOSED" if closed else "OPEN",
        comments=comments,
        labels=labels,
    )
    return fleet.Story(
        number=number,
        repo=repo,
        title="T",
        status="Done" if closed else "Backlog",
        points=1,
        closed=closed,
        item=f"I_{number}",
        issue=held,
        parent=parent,
        closed_at=closed_at or (_at(3) if closed else ""),
    )


def _finished(number: int, days: int, **kwargs) -> fleet.Story:
    return _story(number, True, closed_at=_ago(days), **kwargs)


def _open(number: int, **kwargs) -> fleet.Story:
    return _story(number, False, **kwargs)


def _fleet(*stories: fleet.Story, archived: tuple[fleet.Story, ...] = ()) -> fleet.Fleet:
    return fleet.Fleet(stories=list(stories), blockers={}, missing=[], archived=list(archived))


# splits


def _parked(number: int, *kids: int, closed: bool = True) -> fleet.Story:
    log = [("Drafted:", _at(0), f"parked from {REPO}#900")]
    if kids:
        log.append(("Split:", _at(0), "into " + ", ".join(f"#{kid}" for kid in kids)))
    return _story(number, closed, *log)


def _done(number: int, **kwargs) -> fleet.Story:
    return _story(number, True, **kwargs)


def test_splits_ignore_a_story_written_from_a_request():
    root = _story(
        1,
        True,
        ("Drafted:", _at(0), "the story and its plan, from the request."),
        ("Split:", _at(1), "into #2"),
    )

    assert throughput.splits([root, _done(2)]) == []


def test_splits_measure_a_parked_feature_split_into_stories():
    root = _story(
        1, True, ("Drafted:", _at(0), f"parked from {REPO}#900"), ("Split:", _at(1), "into #2, acme/gadgets#3")
    )
    shed = [_done(2), _done(3, repo="acme/gadgets")]

    assert throughput.splits([root, *shed, _parked(4)]) == [1, 3]


def test_splits_count_a_story_shed_later():
    root = _story(
        1, True, ("Drafted:", _at(0), f"parked from {REPO}#900"), ("Split:", _at(1), "#2 The other half, moved out.")
    )

    assert throughput.splits([root, _done(2)]) == [2]


def test_splits_count_a_tree_two_levels_deep():
    shed = _story(2, True, ("Split:", _at(1), "#3 The other half, moved out."))

    assert throughput.splits([_parked(1, 2), shed, _done(3)]) == [3]


def test_splits_ignore_a_parked_feature_that_has_not_finished():
    assert throughput.splits([_parked(1, 2, closed=False), _done(2)]) == []


def test_splits_do_not_count_a_shed_story_as_its_own_root():
    assert throughput.splits([_parked(1, 2), _parked(2)]) == [2]


def test_splits_do_not_count_a_dropped_story_in_a_tree():
    dropped = dataclasses.replace(_done(3), dropped=True)

    assert throughput.splits([_parked(1, 2, 3), _done(2), dropped]) == [2]


def test_splits_do_not_count_a_dropped_parked_feature_as_a_root():
    dropped = dataclasses.replace(_parked(1, 2), dropped=True)

    assert throughput.splits([dropped, _done(2), _parked(3)]) == [1]


def test_thin_splits_draw_the_worst_seen():
    assert throughput._sizes([1, 1, 2, 4]) == [4]


def test_no_splits_draw_one_story():
    assert throughput._sizes([]) == [1]


def test_five_splits_are_drawn_as_measured():
    assert throughput._sizes([1, 1, 2, 2, 4]) == [1, 1, 2, 2, 4]


# weekly


def test_weekly_counts_finishes_in_weeks_that_end_today():
    stories = [_finished(1, 0), _finished(2, 6), _finished(3, 7), _finished(4, 21)]

    assert throughput.weekly(stories, TODAY) == [1, 0, 1, 2]


def test_a_week_with_no_finishes_counts_as_zero():
    assert throughput.weekly([_finished(1, 35)], TODAY) == [1, 0, 0, 0, 0, 0]


def test_weekly_counts_only_finished_stories_deckhand_owns():
    dropped = dataclasses.replace(_finished(2, 0), dropped=True)
    unstamped = dataclasses.replace(_finished(3, 0), closed_at="")
    foreign = _finished(4, 0, labels=())

    assert throughput.weekly([_finished(1, 0), dropped, unstamped, foreign, _open(5)], TODAY) == [1]


def test_no_finishes_are_no_weeks():
    assert throughput.weekly([_open(1)], TODAY) == []


# pace


def _members(count: int, first: int) -> list[fleet.Story]:
    return [_finished(10 + index, first - 7 * index, parent=EPIC) for index in range(count)]


def test_an_epic_with_five_finished_stories_over_four_weeks_is_paced_by_its_own():
    members = _members(5, 28)
    others = [_finished(100 + day, day) for day in range(28)]

    assert throughput.pace([*members, *others], EPIC, TODAY) == ("epic", [1, 1, 1, 1, 1])


def test_an_epic_under_four_weeks_old_is_paced_by_the_project():
    members = [_finished(10 + index, 21 - index) for index in range(5)]
    members = [dataclasses.replace(story, parent=EPIC) for story in members]

    assert throughput.pace(members, EPIC, TODAY)[0] == "project"


def test_an_epic_under_five_finished_stories_is_paced_by_the_project():
    assert throughput.pace(_members(4, 60), EPIC, TODAY)[0] == "project"


def test_the_project_pace_is_its_last_twelve_weeks():
    stories = [_finished(number, 7 * number) for number in range(20)]

    assert throughput.pace(stories, EPIC, TODAY) == ("project", [1] * 12)


# remaining


def test_remaining_counts_open_stories_and_unsplit_drafts_apart():
    settled = draft.render("What it needs.", [draft.Entry(title="A", sentence="B", after=())])
    read = _fleet(
        _open(1, parent=EPIC),
        _open(2, parent=EPIC, body=DRAFT),
        _open(3, parent=EPIC, body=settled),
        _open(4, parent=EPIC, labels=()),
        _open(5),
        _finished(6, 0, parent=EPIC),
    )

    assert throughput.remaining(read, EPIC) == (2, 1)


# simulate


def test_nothing_left_is_no_forecast():
    assert throughput.simulate(0, 0, [1], [1]) is None


def test_no_measured_pace_is_no_forecast():
    assert throughput.simulate(3, 0, [0, 0, 0], [1]) is None
    assert throughput.simulate(3, 0, [], [1]) is None


def test_one_steady_week_makes_every_run_identical():
    assert throughput.simulate(4, 0, [2], [1], runs=50) == {50: 2, 85: 2, 95: 2}
    assert throughput.simulate(5, 0, [2], [1], runs=50) == {50: 3, 85: 3, 95: 3}


def test_an_unsplit_draft_counts_as_a_drawn_split_size():
    assert throughput.simulate(1, 1, [1], [3], runs=50) == {50: 4, 85: 4, 95: 4}


def test_empty_weeks_reach_the_pessimistic_percentiles():
    assert throughput.simulate(1, 0, [0, 1, 1], [1]) == {50: 1, 85: 2, 95: 3}


def test_a_run_stops_counting_at_ten_years():
    assert throughput.simulate(10_000, 0, [1], [1], runs=5) == {50: 520, 85: 520, 95: 520}


def test_a_seed_makes_two_runs_identical():
    first = throughput.simulate(9, 2, [0, 1, 3], [1, 2, 4], runs=200, seed=7)

    assert first == throughput.simulate(9, 2, [0, 1, 3], [1, 2, 4], runs=200, seed=7)


# outlook


def test_an_outlook_is_paced_by_the_project_before_the_epic_has_its_own():
    history = [_finished(100 + week, 7 * week) for week in range(12)]
    read = _fleet(_open(1, parent=EPIC), _open(2, parent=EPIC), archived=tuple(history))

    assert throughput.outlook(read, EPIC, TODAY) == throughput.Outlook(
        left=2,
        floor_weeks=2,
        commitment_weeks=2,
        worst_weeks=2,
        pace="project",
        weeks=12,
        per_week=1.0,
        unsplit=0,
        split_size=1.0,
    )


def test_an_outlook_is_paced_by_the_epic_once_it_has_its_own():
    others = [_finished(100 + day, day) for day in range(35)]
    read = _fleet(*_members(5, 28), _open(1, parent=EPIC), *others)

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.pace, found.weeks, found.per_week, found.commitment_weeks) == ("epic", 5, 1.0, 1)


def test_an_outlook_counts_an_unsplit_draft_by_the_splits_parked_features_had():
    trees = []
    for root in (1, 3, 5, 7, 9):
        trees += [dataclasses.replace(_parked(root, root + 1), closed_at=_ago(root)), _finished(root + 1, root)]
    read = _fleet(*trees, _open(20, parent=EPIC, body=DRAFT))

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.left, found.unsplit, found.split_size) == (1, 1, 2.0)


def test_an_epic_with_nothing_left_has_no_forecast():
    found = throughput.outlook(_fleet(_finished(1, 0, parent=EPIC)), EPIC, TODAY)

    assert (found.left, found.floor_weeks, found.commitment_weeks, found.worst_weeks, found.pace) == (
        0,
        None,
        None,
        None,
        None,
    )


def test_an_outlook_with_no_finished_story_has_no_forecast():
    found = throughput.outlook(_fleet(_open(1, parent=EPIC)), EPIC, TODAY)

    assert (found.left, found.commitment_weeks, found.pace, found.weeks, found.per_week) == (1, None, None, 0, 0.0)


# rows


def _outlook(**changes) -> throughput.Outlook:
    base = throughput.Outlook(
        left=3,
        floor_weeks=2,
        commitment_weeks=4,
        worst_weeks=6,
        pace="project",
        weeks=12,
        per_week=1.5,
        unsplit=0,
        split_size=1.0,
    )
    return dataclasses.replace(base, **changes)


def test_the_rows_give_each_line_in_weeks():
    assert throughput.rows(_outlook()) == [
        "  3 pieces left",
        "",
        "  Floor         2 weeks   50th percentile",
        "  Commitment    4 weeks   85th percentile",
        "  Worst seen    6 weeks   95th percentile",
        "",
        "  Paced by the whole project's last 12 weeks, a median of 1.5 stories a week.",
        "  That includes work outside this epic, so it leans early.",
    ]


def test_the_rows_say_an_epic_was_paced_by_its_own_weeks():
    lines = throughput.rows(_outlook(pace="epic", weeks=5, left=1, floor_weeks=1))

    assert "  Paced by this epic's own 5 weeks, a median of 1.5 stories a week." in lines
    assert "  1 piece left" in lines
    assert "  Floor         1 week    50th percentile" in lines
    assert not any("leans early" in line for line in lines)


def test_the_rows_say_how_an_unsplit_draft_was_counted():
    lines = throughput.rows(_outlook(unsplit=2, split_size=2.5))

    assert lines[-1] == "  2 unsplit drafts counted as 2.5 stories each, the average a finished parked feature became."


def test_the_rows_say_so_when_nothing_is_left():
    assert throughput.rows(_outlook(left=0, floor_weeks=None, pace=None)) == ["  nothing left to forecast"]


def test_the_rows_say_so_when_no_pace_was_measured():
    found = _outlook(floor_weeks=None, commitment_weeks=None, worst_weeks=None, pace=None, weeks=0, per_week=0.0)

    assert throughput.rows(found) == ["  3 pieces left, and no story finished in the weeks measured, so no forecast."]
