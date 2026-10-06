"""How long finished stories took, and when everything left on the board will be done."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta

from deckhand import fleet, forecast, issue

REPO = "acme/widgets"
TODAY = date(2026, 10, 2)
DRAFT = "## Requirements\n\nWhat it needs.\n"


def _entry(prefix: str, at: str) -> issue.Comment:
    return issue.Comment(author="claude", body=f"{prefix} noted", created_at=at)


def _story(
    number: int,
    points: int | None,
    closed: bool,
    *log: tuple[str, str],
    repo: str = REPO,
    labels: tuple[str, ...] = ("deckhand",),
    body: str = "",
    closed_at: str = "",
) -> fleet.Story:
    comments = [_entry(prefix, at) for prefix, at in log]
    ish = issue.Issue(
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
        status="Done" if closed else "In Progress",
        points=points,
        closed=closed,
        item=f"I_{number}",
        issue=ish,
        closed_at=closed_at or ("2026-09-01T00:00:00Z" if closed else ""),
    )


def _plain(number: int, points: int | None, **kwargs) -> fleet.Story:
    return _story(number, points, False, **kwargs)


def _fleet(*stories: fleet.Story, archived: tuple[fleet.Story, ...] = ()) -> fleet.Fleet:
    return fleet.Fleet(stories=list(stories), blockers={}, missing=[], archived=list(archived))


# durations


def test_a_finished_story_reports_the_hours_between_its_two_entries():
    story = _story(1, 1, True, ("Started:", "2026-09-01T00:00:00Z"), ("Pull request:", "2026-09-01T01:48:00Z"))

    assert forecast.durations([story]) == {1: [1.8]}


def test_a_story_with_no_started_entry_is_skipped():
    story = _story(2, 1, True, ("Pull request:", "2026-09-01T01:00:00Z"))

    assert forecast.durations([story]) == {}


def test_a_story_that_has_not_closed_is_not_measured():
    story = _story(3, 1, False, ("Started:", "2026-09-01T00:00:00Z"), ("Pull request:", "2026-09-01T01:00:00Z"))

    assert forecast.durations([story]) == {}


def test_durations_band_by_points():
    one = _story(4, 1, True, ("Started:", "2026-09-01T00:00:00Z"), ("Pull request:", "2026-09-01T01:00:00Z"))
    also_one = _story(5, 1, True, ("Started:", "2026-09-01T00:00:00Z"), ("Pull request:", "2026-09-01T00:30:00Z"))
    two = _story(6, 2, True, ("Started:", "2026-09-01T00:00:00Z"), ("Pull request:", "2026-09-01T02:00:00Z"))

    found = forecast.durations([one, also_one, two])

    assert found == {1: [0.5, 1.0], 2: [2.0]}


# critical path


def _ran(number: int, start: str, end: str, points: int | None = 1) -> fleet.Story:
    return _story(number, points, True, ("Started:", start), ("Pull request:", end))


def test_a_serial_chain_is_the_sum_of_its_stories():
    stories = [_plain(1, 1), _plain(2, 1), _plain(3, 1)]
    blockers = {(REPO, 2): [(REPO, 1, "")], (REPO, 3): [(REPO, 2, "")]}

    assert forecast.critical_path(stories, blockers, {1: [2.0]}) == 6.0


def test_independent_stories_are_as_long_as_the_longest():
    stories = [_plain(1, 1), _plain(2, 2)]

    assert forecast.critical_path(stories, {}, {1: [1.0], 2: [5.0]}) == 5.0


def test_a_band_is_its_median_story():
    assert forecast.critical_path([_plain(1, 1)], {}, {1: [1.0, 2.0, 30.0]}) == 2.0


def test_a_missing_band_falls_back_to_the_median_of_the_bands():
    history = {1: [1.0], 2: [2.0], 5: [9.0]}

    assert forecast.critical_path([_plain(1, 3)], {}, history) == 2.0


def test_no_finished_story_is_no_critical_path():
    assert forecast.critical_path([_plain(1, 1)], {}, {}) is None


def test_hours_measures_one_finished_story():
    story = _story(1, 1, True, ("Started:", "2026-09-01T00:00:00Z"), ("Pull request:", "2026-09-01T03:00:00Z"))

    assert forecast.hours(story) == 3.0


def test_hours_is_none_without_both_entries():
    assert forecast.hours(_story(2, 1, True, ("Pull request:", "2026-09-01T01:00:00Z"))) is None


# rows


def _done_ago(number: int, days: int) -> fleet.Story:
    noon = datetime.combine(TODAY - timedelta(days=days), datetime.min.time()) + timedelta(hours=12)
    at = noon.astimezone()
    started = (at - timedelta(hours=24)).isoformat()
    return _story(number, 1, True, ("Started:", started), ("Pull request:", at.isoformat()), closed_at=at.isoformat())


# Nine weeks of finishes, two in each odd week and one in each even one, every story taking a day.
PACED = [_done_ago(100 + index, days) for index, days in enumerate([0, 7, 8, 14, 21, 22, 28, 35, 36, 42, 49, 50, 56])]


def test_the_board_is_forecast_from_its_weekly_finishes():
    read = _fleet(*[_plain(n, 1) for n in range(1, 7)], archived=tuple(PACED))

    assert forecast.rows(read, TODAY) == [
        "  6 stories left",
        "",
        "  Likely done by          5 weeks   90th percentile",
        "  Possibly as early as    4 weeks   50th percentile",
        "",
        "  Paced by the board's last 8 weeks, an average of 1.5 stories a week.",
        "",
        "  Critical path through the blockers: 1 day at the median pace",
    ]


def test_the_critical_path_follows_the_blockers():
    read = fleet.Fleet(
        stories=[_plain(1, 1), _plain(2, 1), _plain(3, 1)],
        blockers={(REPO, 2): [(REPO, 1, "")], (REPO, 3): [(REPO, 2, "")]},
        missing=[],
        archived=list(PACED),
    )

    assert forecast.rows(read, TODAY)[-1] == "  Critical path through the blockers: 3 days at the median pace"


def test_an_unsplit_draft_counts_as_a_split_and_says_so():
    read = _fleet(_plain(1, None, body=DRAFT), archived=tuple(PACED))

    assert "  1 draft not yet split, counted as 1 story, since no parked feature has finished yet." in forecast.rows(
        read, TODAY
    )


def test_too_few_finishes_give_the_reason_and_no_range():
    read = _fleet(_plain(1, 1), archived=(*PACED[:3], PACED[-1]))

    assert forecast.rows(read, TODAY) == [
        "  1 story left",
        "",
        "  Fewer than 5 stories finished on the board in the weeks measured, so there is no date range yet.",
        "",
        "  Critical path through the blockers: 1 day at the median pace",
    ]


def test_a_board_whose_work_began_lately_has_no_range():
    read = _fleet(_plain(1, 1), archived=tuple(PACED[:4]))

    assert forecast.rows(read, TODAY)[2] == (
        "  Work on the board began fewer than 4 weeks ago, so there is no date range yet."
    )


def test_the_block_never_prints_a_worst_case():
    lines = forecast.rows(_fleet(_plain(1, 1), archived=tuple(PACED)), TODAY)

    assert not any("Worst" in line or "99th" in line for line in lines)


def test_no_finished_story_is_no_range_and_no_critical_path():
    assert forecast.rows(_fleet(_plain(1, 1)), TODAY) == [
        "  1 story left",
        "",
        "  Work on the board began fewer than 4 weeks ago, so there is no date range yet.",
    ]


# what is forecast


def test_an_issue_the_process_does_not_own_is_not_forecast():
    read = _fleet(
        _plain(2, 1), _story(3, 1, False, labels=()), _story(4, 1, False, labels=("bug",)), archived=tuple(PACED)
    )

    assert forecast.rows(read, TODAY)[0] == "  1 story left"


def test_a_board_of_issues_the_process_does_not_own_has_nothing_to_forecast():
    read = _fleet(_story(2, 1, False, labels=()), archived=tuple(PACED))

    assert forecast.rows(read, TODAY) == ["  nothing left to forecast"]


def test_a_dropped_story_is_not_left_to_forecast():
    read = _fleet(_plain(1, 1), dataclasses.replace(_plain(2, 1), dropped=True), archived=tuple(PACED))

    assert forecast.rows(read, TODAY)[0] == "  1 story left"
