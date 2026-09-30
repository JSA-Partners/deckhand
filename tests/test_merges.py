"""What to merge first: every open pull request, its checks, main, the work it frees, and what it shares."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from deckhand import columns, fleet, issue, merges

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "acme/widgets"
OTHER = "acme/gadgets"


def _nodes() -> list[dict]:
    data = json.loads((FIXTURES / "captain-items.json").read_text(encoding="utf-8"))
    return data["data"]["organization"]["projectV2"]["items"]["nodes"]


def _story(number: int, points: int | None = None, repo: str = REPO, status: str = columns.IN_REVIEW) -> fleet.Story:
    story = {story.number: story for story in fleet.stories(_nodes())}[253]
    return dataclasses.replace(story, number=number, repo=repo, status=status, points=points)


def _pull(
    number: int,
    files: tuple[str, ...] = (),
    behind: bool | None = False,
    failed: tuple[str, ...] = (),
    pending: int = 0,
) -> issue.PullRequest:
    url = f"https://github.com/acme/widgets/pull/{number}"
    return issue.PullRequest(number, url, "OPEN", False, behind, failed, pending, files)


def _fleet(
    pulls: dict[fleet.Key, issue.PullRequest],
    stories: list[fleet.Story] | None = None,
    blockers: fleet.Blockers | None = None,
) -> fleet.Fleet:
    found = stories if stories is not None else [_story(number, repo=repo) for repo, number in pulls]
    return fleet.Fleet(stories=found, blockers=blockers or {}, missing=[], pulls=pulls)


def _table(lines: list[str]) -> list[list[str]]:
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines[2:] if line.startswith("| ")]


def _order(lines: list[str]) -> list[str]:
    return [row[0] for row in _table(lines)]


def _row(lines: list[str], stories: int | str) -> list[str]:
    (row,) = [row for row in _table(lines) if row[0] == str(stories)]
    return row


PR = 2
CHECKS = 3
MAIN = 4
UNBLOCKS = 5
SHARES = 6


def test_no_open_pull_request_says_so():
    assert merges.rows(_fleet({}), REPO) == ["  nothing in review"]


def test_a_row_names_the_story_its_repository_and_its_pull_request():
    lines = merges.rows(_fleet({(REPO, 272): _pull(1272)}), REPO)
    assert lines[0] == "| # | Repo | PR | Checks | Main | Unblocks | Shares files with |"
    assert _row(lines, 272)[:3] == ["272", "widgets", "#1272"]


def test_a_pull_request_on_a_story_that_is_not_the_processs_is_left_out():
    foreign = _story(2)
    foreign = dataclasses.replace(foreign, issue=dataclasses.replace(foreign.issue, labels=()))
    pulls = {(REPO, 1): _pull(11, ("a.go",)), (REPO, 2): _pull(12, ("a.go",))}
    lines = merges.rows(_fleet(pulls, [_story(1), foreign]), REPO)
    assert _order(lines) == ["1"]
    assert _row(lines, 1)[SHARES] == "-"


def test_only_foreign_pull_requests_is_nothing_in_review():
    foreign = _story(2)
    foreign = dataclasses.replace(foreign, issue=dataclasses.replace(foreign.issue, labels=()))
    assert merges.rows(_fleet({(REPO, 2): _pull(12)}, [foreign]), REPO) == ["  nothing in review"]


def test_one_pull_request_closing_several_stories_is_one_row_naming_them_all():
    pulls = {(REPO, 57): _pull(114), (REPO, 7): _pull(114), (REPO, 11): _pull(114), (REPO, 96): _pull(111)}
    lines = merges.rows(_fleet(pulls), REPO)
    assert _order(lines) == ["96", "7, 11, 57"]
    assert _row(lines, "7, 11, 57")[PR] == "#114"


def test_checks_say_passed_running_or_which_failed():
    pulls = {
        (REPO, 1): _pull(11),
        (REPO, 2): _pull(12, pending=2),
        (REPO, 3): _pull(13, failed=("Unit tests", "Lint"), pending=1),
    }
    lines = merges.rows(_fleet(pulls), REPO)
    assert _row(lines, 1)[CHECKS] == "passed"
    assert _row(lines, 2)[CHECKS] == "2 running"
    assert _row(lines, 3)[CHECKS] == "failed: Unit tests, Lint"


def test_main_says_current_behind_or_unknown():
    pulls = {(REPO, 1): _pull(11, behind=False), (REPO, 2): _pull(12, behind=True), (REPO, 3): _pull(13, behind=None)}
    lines = merges.rows(_fleet(pulls), REPO)
    assert _row(lines, 1)[MAIN] == "current"
    assert _row(lines, 2)[MAIN] == "behind"
    assert _row(lines, 3)[MAIN] == "unknown"


def test_unblocks_is_the_points_of_everything_waiting_on_the_story_down_the_chain():
    backlog = [_story(5, points=3, status=columns.BACKLOG), _story(6, points=2, status=columns.BACKLOG)]
    blockers = {(REPO, 5): [(REPO, 1, "One")], (REPO, 6): [(REPO, 5, "Five")]}
    pulls = {(REPO, 1): _pull(11), (REPO, 2): _pull(12)}
    lines = merges.rows(_fleet(pulls, [_story(1), _story(2), *backlog], blockers), REPO)
    assert _row(lines, 1)[UNBLOCKS] == "5 pts"
    assert _row(lines, 2)[UNBLOCKS] == "-"


def test_unblocks_sums_what_waits_on_every_story_the_pull_request_closes():
    backlog = [_story(5, points=3, status=columns.BACKLOG), _story(6, points=2, status=columns.BACKLOG)]
    blockers = {(REPO, 5): [(REPO, 1, "One")], (REPO, 6): [(REPO, 2, "Two")]}
    pulls = {(REPO, 1): _pull(11), (REPO, 2): _pull(11)}
    lines = merges.rows(_fleet(pulls, [_story(1), _story(2), *backlog], blockers), REPO)
    assert _row(lines, "1, 2")[UNBLOCKS] == "5 pts"


def test_unblocks_counts_a_story_waiting_on_several_the_pull_request_closes_once():
    backlog = [_story(5, points=3, status=columns.BACKLOG)]
    blockers = {(REPO, 5): [(REPO, 1, "One"), (REPO, 2, "Two")], (REPO, 2): [(REPO, 1, "One")]}
    pulls = {(REPO, 1): _pull(11), (REPO, 2): _pull(11)}
    lines = merges.rows(_fleet(pulls, [_story(1, points=8), _story(2, points=8), *backlog], blockers), REPO)
    assert _row(lines, "1, 2")[UNBLOCKS] == "3 pts"


def test_shares_names_each_other_pull_request_changing_a_common_path_and_how_many():
    pulls = {
        (REPO, 1): _pull(11, ("a.go", "b.go", "c.go")),
        (REPO, 2): _pull(12, ("a.go", "b.go")),
        (REPO, 3): _pull(13, ("c.go",)),
        (REPO, 4): _pull(14, ("d.go",)),
    }
    lines = merges.rows(_fleet(pulls), REPO)
    assert _row(lines, 1)[SHARES] == "#12 (2 files), #13 (1 file)"
    assert _row(lines, 2)[SHARES] == "#11 (2 files)"
    assert _row(lines, 4)[SHARES] == "-"


def test_a_pull_request_shares_nothing_with_itself():
    pulls = {(REPO, 1): _pull(11, ("a.go",)), (REPO, 2): _pull(11, ("a.go",)), (REPO, 3): _pull(13, ("a.go",))}
    lines = merges.rows(_fleet(pulls), REPO)
    assert _row(lines, "1, 2")[SHARES] == "#13 (1 file)"
    assert _row(lines, 3)[SHARES] == "#11 (1 file)"


def test_the_same_path_in_another_repository_is_not_shared():
    pulls = {(REPO, 1): _pull(11, ("a.go",)), (OTHER, 2): _pull(12, ("a.go",))}
    lines = merges.rows(_fleet(pulls), REPO)
    assert _row(lines, 1)[SHARES] == "-"


def test_a_pull_request_in_another_repository_is_named_in_full():
    pulls = {(REPO, 1): _pull(11, ("a.go",)), (OTHER, 2): _pull(12, ("a.go",)), (OTHER, 3): _pull(13, ("a.go",))}
    lines = merges.rows(_fleet(pulls), REPO)
    assert _row(lines, 2)[SHARES] == "acme/gadgets#13 (1 file)"


def test_a_pull_request_ready_to_merge_goes_before_one_that_is_not():
    pulls = {
        (REPO, 1): _pull(11, failed=("Lint",)),
        (REPO, 2): _pull(12, pending=1),
        (REPO, 3): _pull(13, behind=True),
        (REPO, 4): _pull(14, behind=None),
        (REPO, 5): _pull(15),
    }
    assert _order(merges.rows(_fleet(pulls), REPO))[0] == "5"


def test_among_ready_ones_the_one_that_unblocks_more_points_goes_first():
    backlog = [_story(7, points=1, status=columns.BACKLOG), _story(8, points=5, status=columns.BACKLOG)]
    blockers = {(REPO, 7): [(REPO, 1, "One")], (REPO, 8): [(REPO, 2, "Two")]}
    pulls = {(REPO, 1): _pull(11), (REPO, 2): _pull(12)}
    lines = merges.rows(_fleet(pulls, [_story(1), _story(2), *backlog], blockers), REPO)
    assert _order(lines) == ["2", "1"]


def test_equal_points_put_the_one_sharing_files_with_fewer_others_first():
    pulls = {
        (REPO, 1): _pull(11, ("a.go", "b.go")),
        (REPO, 2): _pull(12, ("a.go",)),
        (REPO, 3): _pull(13, ("b.go",), failed=("Lint",)),
    }
    assert _order(merges.rows(_fleet(pulls), REPO)) == ["2", "1", "3"]


def test_equal_again_goes_by_pull_request_number():
    pulls = {(REPO, 4): _pull(19), (REPO, 9): _pull(14)}
    assert _order(merges.rows(_fleet(pulls), REPO)) == ["9", "4"]


def test_the_closing_line_names_the_first_pull_request_and_which_updates_after_it():
    pulls = {(REPO, 272): _pull(114, ("a.go", "b.go")), (REPO, 259): _pull(111, ("a.go", "b.go"), pending=1)}
    lines = merges.rows(_fleet(pulls), REPO)
    assert lines[-2] == ""
    assert lines[-1] == "Merge #114 first; #111 shares 2 files with it and updates after."


def test_the_closing_line_names_every_pull_request_that_updates_after():
    pulls = {
        (REPO, 1): _pull(11, ("a.go", "b.go")),
        (REPO, 2): _pull(12, ("a.go",), pending=1),
        (REPO, 3): _pull(13, ("a.go", "b.go"), pending=1),
    }
    lines = merges.rows(_fleet(pulls), REPO)
    assert lines[-1] == "Merge #11 first; #12 (1 file) and #13 (2 files) share files with it and update after."


def test_the_closing_line_says_when_nothing_shares_a_file_with_the_first():
    lines = merges.rows(_fleet({(REPO, 1): _pull(11, ("a.go",))}), REPO)
    assert lines[-1] == "Merge #11 first; no other open pull request changes its files."


def test_the_closing_line_says_when_nothing_is_ready_to_merge():
    pulls = {(REPO, 1): _pull(11, ("a.go",), behind=True), (REPO, 2): _pull(12, ("a.go",), pending=1)}
    lines = merges.rows(_fleet(pulls), REPO)
    assert lines[-1] == (
        "Nothing is ready to merge yet, and #11 goes first once it is; #12 shares 1 file with it and updates after."
    )
