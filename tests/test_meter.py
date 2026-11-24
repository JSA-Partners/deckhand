from __future__ import annotations

from deckhand.linear.meter import UNKNOWN_WAIT, Meter, clock, name_of, wait_words

NOW = 1_763_546_400.0
HEADERS = {
    "X-Complexity": "1250",
    "X-RateLimit-Complexity-Limit": "2000000",
    "X-RateLimit-Complexity-Remaining": "1500000",
    "X-RateLimit-Complexity-Reset": str(int((NOW + 600) * 1000)),
}


def test_a_query_is_named_by_its_operation() -> None:
    assert name_of("query ProjectIssues($id: String!) { x }") == "ProjectIssues"
    assert name_of("mutation IssueCreate($input: X!) { y }") == "IssueCreate"
    assert name_of("{ viewer { id } }") == "anonymous"


def test_nothing_heard_is_nothing_taken() -> None:
    assert Meter(lambda: NOW).taken() is None


def test_a_reply_keeps_the_budget_and_the_cost_of_its_query() -> None:
    meter = Meter(lambda: NOW)
    assert meter.heard("ProjectIssues", HEADERS, False) is None
    budget = {"limit": 2000000, "remaining": 1500000, "reset": NOW + 600, "cost": 1250, "at": NOW, "until": None}
    assert meter.taken() == {"budget": budget, "costs": {"ProjectIssues": 1250}}
    assert meter.taken() is None


def test_each_query_name_keeps_its_first_cost() -> None:
    meter = Meter(lambda: NOW)
    meter.heard("Issue", {"X-Complexity": "40"}, False)
    meter.heard("Issue", {"X-Complexity": "41"}, False)
    meter.heard("Viewer", {"X-Complexity": "1"}, False)
    assert meter.taken()["costs"] == {"Issue": 40, "Viewer": 1}


def test_a_limited_reply_waits_for_the_reset() -> None:
    meter = Meter(lambda: NOW)
    assert meter.heard("Issue", HEADERS, True) == NOW + 600


def test_retry_after_wins_over_the_reset() -> None:
    meter = Meter(lambda: NOW)
    assert meter.heard("Issue", HEADERS | {"Retry-After": "90"}, True) == NOW + 90


def test_a_limited_reply_without_headers_waits_a_default() -> None:
    assert Meter(lambda: NOW).heard("Issue", {}, True) == NOW + UNKNOWN_WAIT


def test_a_reset_in_seconds_is_read_as_seconds() -> None:
    meter = Meter(lambda: NOW)
    assert meter.heard("Issue", {"X-RateLimit-Complexity-Reset": str(int(NOW + 30))}, True) == NOW + 30


def test_a_later_reply_clears_the_limit_and_keeps_the_last_known_budget() -> None:
    meter = Meter(lambda: NOW)
    meter.heard("Issue", HEADERS, True)
    meter.heard("Viewer", {}, False)
    taken = meter.taken()["budget"]
    assert (taken["until"], taken["remaining"]) == (None, 1500000)


def test_the_wait_names_the_clock_time_and_the_minutes() -> None:
    assert wait_words(NOW + 600, NOW) == f"{clock(NOW + 600)}, in 10 minutes"
    assert wait_words(NOW + 30, NOW) == f"{clock(NOW + 30)}, in 1 minute"
