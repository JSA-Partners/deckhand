"""How many stories finish a week, and how many weeks what is left of an epic will take."""

from __future__ import annotations

import dataclasses
import time
from datetime import UTC, date, datetime, timedelta

import pytest

from deckhand import draft, fleet, issue, throughput

REPO = "acme/widgets"
EPIC = (REPO, 300)
TODAY = date(2026, 10, 2)
DRAFT = "## Requirements\n\nWhat it needs.\n"


@pytest.fixture
def pacific(monkeypatch):
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _at(hours: float) -> str:
    return (datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(days: int) -> str:
    noon = datetime.combine(TODAY - timedelta(days=days), datetime.min.time()) + timedelta(hours=12)
    return noon.astimezone().isoformat()


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
    assert throughput._sizes([1, 1, 2, 4]) == ("largest", [4])


def test_no_splits_draw_one_story():
    assert throughput._sizes([]) == ("assumed", [1])


def test_five_splits_are_drawn_as_measured():
    assert throughput._sizes([1, 1, 2, 2, 4]) == ("measured", [1, 1, 2, 2, 4])


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


def test_a_finish_counts_on_its_local_calendar_day(pacific):
    late_evening = _story(1, True, closed_at="2026-09-26T03:00:00Z")

    assert throughput.weekly([late_evening], TODAY) == [1, 0]


def test_a_finish_dated_after_today_is_not_counted():
    assert throughput.weekly([_finished(1, 7), _finished(2, -1)], TODAY) == [1, 0]


def test_the_weeks_start_at_the_week_given():
    assert throughput.weekly([_finished(1, 7)], TODAY, TODAY - timedelta(days=21)) == [0, 0, 1, 0]


# pace


def _members(count: int, first: int) -> list[fleet.Story]:
    return [_finished(10 + index, first - 7 * index, parent=EPIC) for index in range(count)]


def _started(number: int, began: int, finished: int | None = None) -> fleet.Story:
    log = ("Started:", _ago(began), "on the branch")
    if finished is None:
        return _story(number, False, log, parent=EPIC)
    return _story(number, True, log, parent=EPIC, closed_at=_ago(finished))


def test_an_epic_is_paced_by_its_own_finishes_alone():
    members = _members(5, 28)
    others = [_finished(100 + day, day) for day in range(28)]

    assert throughput.pace([*members, *others], EPIC, TODAY) == ([1, 1, 1, 1], True)


def test_an_epic_settles_on_the_twenty_eighth_day_after_its_first_finish():
    def _epic(first: int) -> list[fleet.Story]:
        return [_finished(10 + index, day, parent=EPIC) for index, day in enumerate([first, 0, 0, 0, 0])]

    assert throughput.pace(_epic(27), EPIC, TODAY)[1] is False
    assert throughput.pace(_epic(28), EPIC, TODAY)[1] is True


def test_the_history_starts_when_the_first_member_started():
    members = [_started(20, 35, 14), _finished(21, 14, parent=EPIC), *_members(2, 7)]

    assert throughput.pace(members, EPIC, TODAY) == ([0, 0, 2, 1, 1], True)


def test_without_a_start_the_history_starts_at_the_first_finish():
    members = [_finished(20, 14, parent=EPIC), _finished(21, 14, parent=EPIC), *_members(2, 7)]

    assert throughput.pace(members, EPIC, TODAY) == ([1, 1], False)


def test_an_open_member_started_long_ago_starts_the_history():
    assert throughput.pace([_started(10, 35)], EPIC, TODAY) == ([0] * 5, True)


def test_the_epic_pace_is_its_last_twelve_weeks():
    members = [_finished(10 + week, 7 * week, parent=EPIC) for week in range(20)]

    assert throughput.pace(members, EPIC, TODAY) == ([1] * 12, True)


def test_the_oldest_week_is_left_out_when_work_began_partway_through_it():
    members = [_started(20, 30, 29), *_members(4, 21)]

    assert throughput.pace(members, EPIC, TODAY)[0] == [1, 1, 1, 1]


def test_the_oldest_week_counts_when_work_began_on_its_first_day():
    members = [_started(20, 34, 29), *_members(4, 21)]

    assert throughput.pace(members, EPIC, TODAY)[0] == [1, 1, 1, 1, 1]


def test_an_epic_with_no_member_worked_on_has_no_history():
    assert throughput.pace([_finished(1, 60), _open(2, parent=EPIC)], EPIC, TODAY) == ([], False)


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
    assert throughput.simulate(4, 0, [2], [1], runs=50) == {50: 2, 85: 2, 99: 2}
    assert throughput.simulate(5, 0, [2], [1], runs=50) == {50: 3, 85: 3, 99: 3}


def test_an_unsplit_draft_counts_as_a_drawn_split_size():
    assert throughput.simulate(1, 1, [1], [3], runs=50) == {50: 4, 85: 4, 99: 4}


def test_empty_weeks_reach_the_pessimistic_percentiles():
    assert throughput.simulate(1, 0, [0, 1, 1], [1]) == {50: 1, 85: 3, 99: None}


def test_each_run_resamples_the_weeks_so_a_short_history_runs_wide():
    # A quarter of the runs resample [1, 3] as two slow weeks and take all six.
    assert throughput.simulate(6, 0, [1, 3], [1], runs=2000)[throughput.COMMITMENT] == 6


def test_the_floor_commitment_and_worst_are_the_50th_85th_and_99th():
    assert throughput.PERCENTILES == (50, 85, 99)


def test_an_epic_settles_four_weeks_after_its_work_began():
    assert throughput.SETTLED == 4


def test_a_commitment_two_years_out_is_no_forecast():
    assert throughput.simulate(103, 0, [1], [1], runs=5) == {50: 103, 85: 103, 99: 103}
    assert throughput.simulate(104, 0, [1], [1], runs=5) is None


def test_a_worst_case_at_two_years_is_not_measured():
    found = throughput.simulate(1, 0, [0, 1, 1], [1])

    assert found is not None
    assert found[throughput.COMMITMENT] is not None
    assert found[throughput.WORST] is None


def test_a_seed_makes_two_runs_identical():
    first = throughput.simulate(9, 2, [0, 1, 3], [1, 2, 4], runs=200, seed=7)

    assert first == throughput.simulate(9, 2, [0, 1, 3], [1, 2, 4], runs=200, seed=7)


# outlook


def _weekly_members(weeks: int) -> list[fleet.Story]:
    return [_finished(100 + week, 7 * week, parent=EPIC) for week in range(weeks)]


def _varied_members() -> list[fleet.Story]:
    return [*_weekly_members(13), _finished(99, 3, parent=EPIC)]


def test_an_outlook_is_paced_by_the_epic_alone():
    others = [_finished(500 + day, day) for day in range(84)]
    read = _fleet(_open(1, parent=EPIC), _open(2, parent=EPIC), *others, archived=tuple(_varied_members()))

    assert throughput.outlook(read, EPIC, TODAY) == throughput.Outlook(
        left=2,
        floor_weeks=2,
        commitment_weeks=2,
        worst_weeks=2,
        pace="epic",
        weeks=12,
        per_week=1.08,
        unsplit=0,
        split_size=1.0,
        split_basis=None,
        measured=0,
        reason=None,
    )


def test_an_outlook_of_weeks_that_never_varied_is_no_forecast():
    read = _fleet(*_members(9, 56), _started(30, 62), _open(1, parent=EPIC))

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.commitment_weeks, found.pace, found.weeks, found.per_week, found.reason) == (
        None,
        None,
        9,
        1.0,
        throughput.FLAT,
    )


def test_an_outlook_never_falls_back_to_the_project():
    history = [_finished(500 + week, 7 * week) for week in range(12)]
    read = _fleet(_open(1, parent=EPIC), _open(2, parent=EPIC), archived=tuple(history))

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.commitment_weeks, found.pace, found.weeks, found.reason) == (None, None, 0, throughput.THIN)


def test_an_outlook_with_four_finished_members_is_no_forecast():
    others = [_finished(500 + day, day) for day in range(60)]
    read = _fleet(*_members(4, 60), _open(1, parent=EPIC), *others)

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.commitment_weeks, found.pace, found.reason) == (None, None, throughput.FEW)


def test_an_outlook_is_settled_four_weeks_after_a_member_started():
    finishes = [_finished(21 + index, day, parent=EPIC) for index, day in enumerate([0, 7, 14, 21])]
    members = [_started(20, 28, 0), *finishes]
    read = _fleet(*members, _open(1, parent=EPIC))

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.pace, found.weeks, found.per_week, found.reason) == ("epic", 4, 1.25, None)


def test_an_outlook_of_an_epic_finished_long_ago_is_no_forecast():
    burst = [_finished(10 + index, 200, parent=EPIC) for index in range(5)]
    project = [_finished(500 + day, day) for day in range(0, 84, 2)]
    read = _fleet(*burst, *project, *[_open(50 + index, parent=EPIC) for index in range(3)])

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.pace, found.weeks, found.per_week, found.reason) == (None, 12, 0.0, throughput.IDLE)


def test_the_pace_is_the_average_week():
    read = _fleet(_finished(1, 34, parent=EPIC), _open(2, parent=EPIC))

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.weeks, found.per_week) == (5, 0.2)


def test_one_story_finished_today_is_no_forecast():
    read = _fleet(_finished(1, 0, parent=EPIC), *[_open(10 + index, parent=EPIC) for index in range(10)])

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.left, found.floor_weeks, found.commitment_weeks, found.worst_weeks, found.pace) == (
        10,
        None,
        None,
        None,
        None,
    )
    assert (found.weeks, found.reason) == (0, throughput.THIN)


def test_an_outlook_two_years_out_is_no_forecast():
    read = _fleet(*[_open(index, parent=EPIC) for index in range(1, 201)], *_varied_members())

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.left, found.floor_weeks, found.commitment_weeks, found.worst_weeks, found.pace) == (
        200,
        None,
        None,
        None,
        None,
    )
    assert (found.weeks, found.per_week, found.reason) == (12, 1.08, throughput.FAR)


def test_an_outlook_with_no_finish_in_twelve_weeks_is_no_forecast():
    read = _fleet(_finished(1, 7 * 20, parent=EPIC), _open(2, parent=EPIC))

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.commitment_weeks, found.weeks, found.per_week, found.reason) == (None, 12, 0.0, throughput.IDLE)


def _trees(count: int, size: int = 2, start: int = 100) -> list[fleet.Story]:
    trees = []
    for index in range(count):
        root = start + 10 * index
        kids = range(root + 1, root + size)
        trees.append(dataclasses.replace(_parked(root, *kids), closed_at=_ago(index)))
        trees += [_finished(kid, index) for kid in kids]
    return trees


def test_an_outlook_counts_an_unsplit_draft_by_the_splits_parked_features_had():
    read = _fleet(*_trees(5), _open(20, parent=EPIC, body=DRAFT))

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.left, found.unsplit, found.split_size, found.split_basis, found.measured) == (
        1,
        1,
        2.0,
        "measured",
        5,
    )


def test_an_outlook_counts_an_unsplit_draft_as_the_largest_of_a_few_splits():
    read = _fleet(*_trees(1, size=3), *_trees(1, start=200), _open(20, parent=EPIC, body=DRAFT))

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.split_size, found.split_basis, found.measured) == (3.0, "largest", 2)


def test_an_outlook_counts_an_unsplit_draft_as_one_story_before_any_split():
    read = _fleet(_finished(1, 0), _open(20, parent=EPIC, body=DRAFT))

    found = throughput.outlook(read, EPIC, TODAY)

    assert (found.split_size, found.split_basis, found.measured) == (1.0, "assumed", 0)


def test_an_outlook_with_no_unsplit_draft_has_no_split_basis():
    found = throughput.outlook(_fleet(*_trees(5), _open(20, parent=EPIC)), EPIC, TODAY)

    assert (found.split_size, found.split_basis) == (1.0, None)


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

    assert (found.left, found.commitment_weeks, found.pace, found.weeks, found.per_week, found.reason) == (
        1,
        None,
        None,
        0,
        0.0,
        throughput.THIN,
    )


# rows


def _outlook(**changes) -> throughput.Outlook:
    base = throughput.Outlook(
        left=3,
        floor_weeks=2,
        commitment_weeks=4,
        worst_weeks=6,
        pace="epic",
        weeks=12,
        per_week=1.5,
        unsplit=0,
        split_size=1.0,
        split_basis=None,
        measured=0,
        reason=None,
    )
    return dataclasses.replace(base, **changes)


def _none(reason: str) -> throughput.Outlook:
    return _outlook(floor_weeks=None, commitment_weeks=None, worst_weeks=None, pace=None, reason=reason)


def test_the_rows_give_each_line_in_weeks():
    assert throughput.rows(_outlook()) == [
        "  3 pieces left",
        "",
        "  Floor         2 weeks   50th percentile",
        "  Commitment    4 weeks   85th percentile",
        "  Worst case    6 weeks   99th percentile",
        "",
        "  Paced by this feature's own 12 weeks, an average of 1.5 stories a week.",
    ]


def test_the_rows_say_one_week_and_one_piece():
    lines = throughput.rows(_outlook(weeks=5, left=1, floor_weeks=1))

    assert "  Paced by this feature's own 5 weeks, an average of 1.5 stories a week." in lines
    assert "  1 piece left" in lines
    assert "  Floor         1 week    50th percentile" in lines


def test_the_rows_say_an_unsplit_draft_was_counted_as_the_measured_average():
    lines = throughput.rows(_outlook(unsplit=2, split_size=2.4, split_basis="measured", measured=7))

    assert lines[-1] == (
        "  2 drafts not yet split, each counted as 2.4 stories, the average of 7 finished parked features."
    )


def test_the_rows_say_an_unsplit_draft_was_counted_as_the_largest_seen():
    lines = throughput.rows(_outlook(unsplit=2, split_size=3.0, split_basis="largest", measured=2))

    assert lines[-1] == (
        "  2 drafts not yet split, each counted as 3 stories, the largest of 2 finished parked features."
    )


def test_the_rows_say_an_unsplit_draft_was_counted_as_one_story_by_assumption():
    lines = throughput.rows(_outlook(unsplit=1, split_size=1.0, split_basis="assumed"))

    assert lines[-1] == "  1 draft not yet split, counted as 1 story, since no parked feature has finished yet."


def test_the_rows_say_so_when_nothing_is_left():
    assert throughput.rows(_outlook(left=0, floor_weeks=None, pace=None)) == ["  nothing left to forecast"]


def test_the_rows_say_so_when_the_history_is_too_short():
    assert throughput.rows(_none(throughput.THIN)) == [
        "  3 pieces left",
        "",
        "  Work on this feature began fewer than 4 weeks ago, so there is no date range yet.",
    ]


def test_the_rows_say_so_when_too_few_stories_finished():
    assert throughput.rows(_none(throughput.FEW))[-1] == (
        "  Fewer than 5 of this feature's stories have finished in the weeks measured, so there is no date range yet."
    )


def test_the_rows_say_so_when_nothing_finished_in_the_weeks_measured():
    assert throughput.rows(_none(throughput.IDLE))[-1] == (
        "  No story of this feature finished in the weeks measured, so there is no date range."
    )


def test_the_rows_say_so_when_the_weekly_pace_has_not_varied():
    assert throughput.rows(_none(throughput.FLAT))[-1] == (
        "  This feature's weekly pace has not varied yet, so a date range would be falsely precise."
    )


def test_the_rows_say_a_worst_case_past_two_years_is_beyond_them():
    assert "  Worst case  beyond two years" in throughput.rows(_outlook(worst_weeks=None))


def test_the_rows_say_so_when_the_pace_would_take_over_two_years():
    assert throughput.rows(_none(throughput.FAR))[-1] == (
        "  The measured pace would take over two years, so there is no date range."
    )
