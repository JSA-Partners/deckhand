"""How long finished stories took, and how long a batch of them will take."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from deckhand import fleet, forecast, issue

REPO = "acme/widgets"
EPIC = (REPO, 300)
DRAFT = "## Requirements\n\nWhat it needs.\n"


def _entry(prefix: str, at: str, text: str = "noted") -> issue.Comment:
    return issue.Comment(author="claude", body=f"{prefix} {text}", created_at=at)


def _story(
    number: int,
    points: int | None,
    closed: bool,
    *log: tuple[str, ...],
    repo: str = REPO,
    labels: tuple[str, ...] = ("deckhand",),
    parent: tuple[str, int] | None = None,
    body: str = "",
) -> fleet.Story:
    comments = [_entry(*entry) for entry in log]
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
        parent=parent,
    )


def _plain(number: int, points: int | None, repo: str = REPO) -> fleet.Story:
    return _story(number, points, False)


def _at(hours: float) -> str:
    return (datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _done(number: int, points: int | None, hours: float, start: float = 0.0, **kwargs) -> fleet.Story:
    return _story(number, points, True, ("Started:", _at(start)), ("Pull request:", _at(start + hours)), **kwargs)


def _fleet(*stories: fleet.Story) -> fleet.Fleet:
    return fleet.Fleet(stories=list(stories), blockers={}, missing=[])


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


# floor


def test_a_serial_chain_is_the_same_length_regardless_of_sessions():
    stories = [_plain(1, 1), _plain(2, 1), _plain(3, 1), _plain(4, 1)]
    blockers = {
        (REPO, 2): [(REPO, 1, "")],
        (REPO, 3): [(REPO, 2, "")],
        (REPO, 4): [(REPO, 3, "")],
    }
    hours = {1: 1.0}

    assert forecast.floor(stories, blockers, 1, hours) == 4.0
    assert forecast.floor(stories, blockers, 8, hours) == 4.0


def test_independent_stories_split_across_sessions():
    stories = [_plain(1, 1), _plain(2, 1), _plain(3, 1), _plain(4, 1)]
    hours = {1: 1.0}

    assert forecast.floor(stories, {}, 2, hours) == 2.0
    assert forecast.floor(stories, {}, 4, hours) == 1.0


def test_two_chains_on_one_session_is_the_total_work_not_the_critical_path():
    stories = [_plain(1, 1), _plain(2, 1), _plain(3, 1), _plain(4, 1)]
    blockers = {
        (REPO, 2): [(REPO, 1, "")],
        (REPO, 4): [(REPO, 3, "")],
    }
    hours = {1: 1.0}

    assert forecast.floor(stories, blockers, 1, hours) == 4.0


def test_a_missing_band_falls_back_to_the_median_of_the_hours_given():
    stories = [_plain(1, 3)]
    hours = {1: 1.0, 2: 2.0, 5: 9.0}

    assert forecast.floor(stories, {}, 1, hours) == 2.0


# simulate


def test_one_sample_per_band_makes_every_run_identical():
    story = _plain(1, 1)
    samples = {1: [2.5]}

    found = forecast.simulate([story], {}, 1, samples, runs=50, seed=1)

    assert found == {50: 2.5, 85: 2.5, 95: 2.5, 100: 2.5}


def test_a_stall_reaches_the_pessimistic_percentile():
    story = _plain(1, 1)
    samples = {1: [1.0, 1.0, 1.0, 88.0]}

    found = forecast.simulate([story], {}, 1, samples, runs=10_000, seed=42)

    assert found[50] == 1.0
    assert found[95] == 88.0
    assert found[100] == 88.0


def test_a_seed_makes_two_runs_identical():
    stories = [_plain(1, 1), _plain(2, 2)]
    blockers = {(REPO, 2): [(REPO, 1, "")]}
    samples = {1: [1.0, 2.0], 2: [3.0, 4.0]}

    first = forecast.simulate(stories, blockers, 2, samples, runs=200, seed=7)
    second = forecast.simulate(stories, blockers, 2, samples, runs=200, seed=7)

    assert first == second


def test_a_band_with_no_samples_falls_back_to_the_pool():
    story = _plain(1, 3)
    samples = {1: [5.0]}

    found = forecast.simulate([story], {}, 1, samples, runs=20, seed=3)

    assert found == {50: 5.0, 85: 5.0, 95: 5.0, 100: 5.0}


def test_hours_measures_one_finished_story():
    story = _story(1, 1, True, ("Started:", "2026-09-01T00:00:00Z"), ("Pull request:", "2026-09-01T03:00:00Z"))

    assert forecast.hours(story) == 3.0


def test_hours_is_none_without_both_entries():
    assert forecast.hours(_story(2, 1, True, ("Pull request:", "2026-09-01T01:00:00Z"))) is None


def _ran(number: int, start: str, end: str) -> fleet.Story:
    return _story(number, 1, True, ("Started:", start), ("Pull request:", end))


def test_concurrency_is_the_median_count_in_progress_at_each_start():
    stories = [
        _ran(1, "2026-09-01T00:00:00Z", "2026-09-01T10:00:00Z"),
        _ran(2, "2026-09-01T01:00:00Z", "2026-09-01T10:00:00Z"),
        _ran(3, "2026-09-01T02:00:00Z", "2026-09-01T10:00:00Z"),
        _ran(4, "2026-09-02T00:00:00Z", "2026-09-02T10:00:00Z"),
        _ran(5, "2026-09-02T01:00:00Z", "2026-09-02T10:00:00Z"),
    ]
    # at each start 1, 2, 3, 1 and 2 stories are in progress; the median is 2
    assert forecast.concurrency(stories) == 2


def test_concurrency_is_unknown_under_five_overlapping_stories():
    stories = [
        _ran(1, "2026-09-01T00:00:00Z", "2026-09-01T01:00:00Z"),
        _ran(2, "2026-09-02T00:00:00Z", "2026-09-02T01:00:00Z"),
    ]
    assert forecast.concurrency(stories) is None


def test_parallel_measures_concurrency_from_archived_stories_too():
    archived = [
        _ran(1, "2026-09-01T00:00:00Z", "2026-09-01T10:00:00Z"),
        _ran(2, "2026-09-01T01:00:00Z", "2026-09-01T10:00:00Z"),
        _ran(3, "2026-09-01T02:00:00Z", "2026-09-01T10:00:00Z"),
        _ran(4, "2026-09-02T00:00:00Z", "2026-09-02T10:00:00Z"),
        _ran(5, "2026-09-02T01:00:00Z", "2026-09-02T10:00:00Z"),
    ]
    read = dataclasses.replace(_fleet(_plain(6, 1)), archived=archived)

    assert forecast.parallel(None, read, live=1) == (2, "measured")


# scope


def test_an_issue_the_process_does_not_own_is_not_forecast():
    read = _fleet(_plain(1, 1), _story(2, 1, False, labels=()))

    assert [story.number for story in forecast.remaining(read)] == [1]


def test_a_scope_keeps_only_that_epics_stories():
    read = _fleet(_story(1, 1, False, parent=EPIC), _plain(2, 1))

    assert [story.number for story in forecast.remaining(read, EPIC)] == [1]


def test_an_outlook_measures_only_the_scope_from_the_whole_history():
    read = _fleet(
        _done(1, 1, 24.0),
        _story(2, 1, False, parent=EPIC),
        _story(3, 1, False, parent=EPIC),
        _plain(4, 1),
    )

    found = forecast.outlook(read, 1, scope=EPIC, seed=1)

    assert (found.left, found.points, found.samples) == (2, 2, 1)
    assert (found.floor, found.commitment, found.worst) == (48.0, 48.0, 48.0)


def test_an_archived_story_still_counts_as_history():
    read = dataclasses.replace(_fleet(_plain(2, 1)), archived=[_done(1, 1, 24.0)])

    assert forecast.outlook(read, 1, seed=1).commitment == 24.0


def test_no_history_is_no_outlook():
    assert forecast.outlook(_fleet(_plain(1, 1)), 1) is None


def test_the_rows_say_so_when_a_scope_has_nothing_left():
    read = _fleet(_done(1, 1, 24.0, parent=EPIC), _plain(2, 1))

    assert forecast.rows(read, 1, "given", scope=EPIC) == ["  nothing left to forecast"]


# drafts


def test_growth_is_one_until_five_finished_roots():
    assert forecast.growth([_done(number, 1, 1.0) for number in range(1, 5)]) == 1.0


def test_growth_is_the_mean_size_of_a_finished_split_tree():
    root = _story(1, 1, True, ("Split:", _at(0), "into #2, acme/gadgets#3"))
    shed = [_done(2, 1, 1.0), _done(3, 1, 1.0, repo="acme/gadgets")]
    alone = [_done(number, 1, 1.0) for number in range(4, 8)]

    # five roots of sizes 3, 1, 1, 1 and 1
    assert forecast.growth([root, *shed, *alone]) == 1.4


def test_growth_ignores_a_root_that_has_not_finished():
    open_root = _story(1, 1, False, ("Split:", _at(0), "into #2, #3"))
    finished = [_done(number, 1, 1.0) for number in range(2, 9)]

    # 2 and 3 were shed, so the roots are 4 to 8, each of size 1
    assert forecast.growth([open_root, *finished]) == 1.0


def test_a_review_is_measured_from_the_latest_draft_to_the_latest_review():
    story = _story(1, 1, True, ("Drafted:", _at(0)), ("Drafted:", _at(96)), ("Review:", _at(102)))

    assert forecast.refinements([story]) == [6.0]


def test_a_story_without_both_entries_measures_no_review():
    assert forecast.refinements([_story(1, 1, True, ("Drafted:", _at(0)))]) == []


def test_a_draft_draws_from_the_pool_grown_and_reviewed():
    story = _story(1, None, False, body=DRAFT)

    found = forecast.simulate(
        [story], {}, 1, {1: [2.0]}, runs=20, seed=1, drafts=frozenset({story.key}), growth=1.5, reviews=[3.0]
    )

    assert found[100] == 6.0


def test_the_floor_counts_a_draft_grown_and_reviewed():
    story = _story(1, None, False, body=DRAFT)

    assert forecast.floor([story], {}, 1, {1: 2.0}, frozenset({story.key}), 1.5, 3.0) == 6.0


def test_an_outlook_counts_its_drafts():
    read = _fleet(_done(1, 1, 24.0), _story(2, None, False, body=DRAFT), _plain(3, 1))

    found = forecast.outlook(read, 1, seed=1)

    assert found.drafts == 1
    assert found.commitment == 48.0


def test_the_rows_say_how_a_draft_was_counted():
    read = _fleet(_done(1, 1, 24.0), _story(2, None, False, body=DRAFT))

    assert "  1 draft counted as 1.0 stories of any size, plus a review." in forecast.rows(read, 1, "given")
