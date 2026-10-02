"""How long finished stories took, and how long a batch of them will take."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from deckhand import draft, fleet, forecast, issue

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


def _parked(number: int, *kids: int, closed: bool = True, review: bool = False) -> fleet.Story:
    log = [("Drafted:", _at(0), f"parked from {REPO}#900")]
    if kids:
        log.append(("Split:", _at(0), "into " + ", ".join(f"#{kid}" for kid in kids)))
    if review:
        log.append(("Review:", _at(1)))
    return _story(number, 1, closed, *log, ("Started:", _at(1)), ("Pull request:", _at(3)))


def _trees(*sizes: int) -> list[fleet.Story]:
    found: list[fleet.Story] = []
    for size in sizes:
        root = len(found) + 1
        kids = range(root + 1, root + size)
        found += [_parked(root, *kids, review=True), *(_done(kid, 1, 2.0, start=1.0) for kid in kids)]
    return found


def test_splits_ignore_a_story_written_from_a_request():
    root = _story(
        1, 1, True, ("Drafted:", _at(0), "the story and its plan, from the request."), ("Split:", _at(1), "into #2")
    )

    assert forecast.splits([root, _done(2, 1, 1.0)]) == []


def test_splits_measure_a_parked_feature_split_into_stories():
    root = _story(
        1, 1, True, ("Drafted:", _at(0), f"parked from {REPO}#900"), ("Split:", _at(1), "into #2, acme/gadgets#3")
    )
    shed = [_done(2, 1, 1.0), _done(3, 1, 1.0, repo="acme/gadgets")]

    assert forecast.splits([root, *shed, _parked(4)]) == [1, 3]


def test_splits_count_a_story_shed_later():
    root = _story(
        1, 1, True, ("Drafted:", _at(0), f"parked from {REPO}#900"), ("Split:", _at(1), "#2 The other half, moved out.")
    )

    assert forecast.splits([root, _done(2, 1, 1.0)]) == [2]


def test_splits_count_a_tree_two_levels_deep():
    shed = _story(2, 1, True, ("Split:", _at(1), "#3 The other half, moved out."))

    assert forecast.splits([_parked(1, 2), shed, _done(3, 1, 1.0)]) == [3]


def test_splits_ignore_a_parked_feature_that_has_not_finished():
    assert forecast.splits([_parked(1, 2, closed=False), _done(2, 1, 1.0)]) == []


def test_splits_do_not_count_a_shed_story_as_its_own_root():
    assert forecast.splits([_parked(1, 2), _parked(2)]) == [2]


def test_thin_splits_draw_the_worst_seen():
    assert forecast._sizes([1, 1, 2, 4]) == [4]


def test_no_splits_draw_one_story():
    assert forecast._sizes([]) == [1]


def test_five_splits_are_drawn_as_measured():
    assert forecast._sizes([1, 1, 2, 2, 4]) == [1, 1, 2, 2, 4]


def test_a_review_is_measured_from_the_latest_draft_to_the_latest_review():
    story = _story(1, 1, True, ("Drafted:", _at(0)), ("Drafted:", _at(96)), ("Review:", _at(102)))

    assert forecast.refinements([story]) == [6.0]


def test_a_story_without_both_entries_measures_no_review():
    assert forecast.refinements([_story(1, 1, True, ("Drafted:", _at(0)))]) == []


def test_a_draft_draws_from_the_pool_past_an_unpointed_band():
    story = _story(1, None, False, body=DRAFT)
    samples = {None: [1.0], 1: [10.0]}
    only = frozenset({story.key})

    found = forecast.simulate([story], {}, 1, samples, runs=200, seed=1, drafts=only, grown=only, sizes=[1])

    assert found[100] == 10.0
    assert forecast.simulate([_plain(2, None)], {}, 1, samples, runs=200, seed=1)[100] == 1.0


def test_a_grown_draft_is_every_story_it_becomes_each_reviewed():
    story = _story(1, None, False, body=DRAFT)
    only = frozenset({story.key})

    grown = forecast.simulate(
        [story], {}, 1, {1: [2.0]}, runs=20, seed=1, drafts=only, grown=only, sizes=[3], reviews=[1.0]
    )
    settled = forecast.simulate([story], {}, 1, {1: [2.0]}, runs=20, seed=1, drafts=only, sizes=[3], reviews=[1.0])

    assert grown == {50: 9.0, 85: 9.0, 95: 9.0, 100: 9.0}
    assert settled == {50: 3.0, 85: 3.0, 95: 3.0, 100: 3.0}


def test_the_floor_counts_a_grown_draft_from_the_pool_each_story_reviewed():
    story = _story(1, None, False, body=DRAFT)
    only = frozenset({story.key})
    hours = {None: 50.0, 1: 2.0}

    assert forecast.floor([story], {}, 1, hours, only, only, size=3, review=1.0, pooled=2.0) == 9.0
    assert forecast.floor([story], {}, 1, hours, only, size=3, review=1.0, pooled=2.0) == 3.0


def test_an_outlook_counts_its_drafts():
    read = _fleet(_done(1, 1, 24.0), _story(2, None, False, body=DRAFT), _plain(3, 1))

    found = forecast.outlook(read, 1, seed=1)

    assert (found.drafts, found.growth) == (1, 1.0)
    assert found.commitment == 48.0


def test_an_outlook_grows_a_draft_by_the_splits_parked_features_had():
    read = _fleet(*_trees(3, 3, 3, 3, 3), _story(20, None, False, body=DRAFT))

    found = forecast.outlook(read, 1, seed=1)

    assert (found.drafts, found.growth, found.thin) == (1, 3.0, False)
    assert (found.floor, found.commitment) == (9.0, 9.0)


def test_an_outlook_with_few_splits_grows_a_draft_by_the_worst_seen():
    read = _fleet(*_trees(2, 4), _story(20, None, False, body=DRAFT))

    found = forecast.outlook(read, 1, seed=1)

    assert (found.drafts, found.growth, found.commitment) == (1, 4.0, 12.0)


def test_an_outlook_does_not_grow_a_draft_that_lists_its_stories():
    settled = draft.render("What it needs.", [draft.Entry(title="A", sentence="B", after=())])
    read = _fleet(*_trees(3, 3, 3, 3, 3), _story(20, None, False, body=settled))

    found = forecast.outlook(read, 1, seed=1)

    assert (found.drafts, found.growth, found.commitment) == (1, 3.0, 3.0)


def test_the_rows_say_how_a_draft_was_counted():
    read = _fleet(_done(1, 1, 24.0), _story(2, None, False, body=DRAFT))

    line = "  1 draft counted as 1.0 stories of any size on average, each with a review."
    assert line in forecast.rows(read, 1, "given")


def test_the_rows_count_several_drafts():
    read = _fleet(*_trees(2, 4), _story(20, None, False, body=DRAFT), _story(21, None, False, body=DRAFT))

    line = "  2 drafts counted as 4.0 stories of any size on average, each with a review."
    assert line in forecast.rows(read, 1, "given")


# idle


def test_idle_is_unknown_under_ten_finished_stories():
    assert forecast.idle([_done(number, 1, 1.0, start=48.0 * number) for number in range(9)]) == 0.0


def test_idle_is_the_share_of_days_with_nothing_in_progress():
    # ten one-hour stories, one every second day: 19 days from first to last, 10 of them worked
    stories = [_done(number, 1, 1.0, start=48.0 * number) for number in range(10)]

    assert forecast.idle(stories) == 9 / 19


def test_a_story_that_spans_days_fills_every_one_of_them():
    stories = [_done(number, 1, 1.0, start=24.0 * number) for number in range(9)]
    stories.append(_done(9, 1, 72.0, start=24.0 * 9))

    # days 0 to 8 by the short ones, 9 to 12 by the long one
    assert forecast.idle(stories) == 0.0


def test_idle_never_stretches_past_the_cap():
    stories = [_done(number, 1, 1.0, start=24.0 * 1000 * number) for number in range(10)]

    assert forecast.idle(stories) == forecast.IDLE_CAP


def test_an_outlook_is_stretched_by_the_idle_share(monkeypatch):
    monkeypatch.setattr(forecast, "idle", lambda stories: 0.5)
    read = _fleet(_done(1, 1, 24.0), _plain(2, 1))

    found = forecast.outlook(read, 1, seed=1)

    assert (found.idle, found.floor, found.commitment, found.worst, found.unbanded) == (0.5, 48.0, 48.0, 48.0, 48.0)


def test_the_rows_say_when_the_forecast_was_stretched(monkeypatch):
    monkeypatch.setattr(forecast, "idle", lambda stories: 0.5)
    read = _fleet(_done(1, 1, 24.0), _plain(2, 1))

    assert "  Stretched for the 50% of days on which nothing was in progress." in forecast.rows(read, 1, "given")
