"""How long finished stories took, and how long a batch of them will take."""

from __future__ import annotations

from deckhand import fleet, forecast, issue

REPO = "acme/widgets"


def _entry(prefix: str, at: str) -> issue.Comment:
    return issue.Comment(author="claude", body=f"{prefix} noted", created_at=at)


def _story(number: int, points: int | None, closed: bool, *log: tuple[str, str], repo: str = REPO) -> fleet.Story:
    comments = [_entry(prefix, at) for prefix, at in log]
    ish = issue.Issue(
        number=number,
        title="T",
        body="",
        url="",
        state="CLOSED" if closed else "OPEN",
        comments=comments,
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
    )


def _plain(number: int, points: int | None, repo: str = REPO) -> fleet.Story:
    return _story(number, points, False)


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
