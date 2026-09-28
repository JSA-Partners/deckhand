"""The columns: one spelling each, and the order a story moves through them."""

from __future__ import annotations

from deckhand import columns


def test_every_column_is_spelled_once_and_the_order_is_the_pipeline():
    assert list(columns.ORDER) == [
        "Draft",
        "Refinement",
        "Ready",
        "Backlog",
        "In Progress",
        "In Review",
        "Verification",
        "Done",
    ]


def test_a_column_is_at_or_past_another_by_the_order_a_story_moves_through_them():
    assert columns.at_least("Ready", "Ready") is True
    assert columns.at_least("Done", "Ready") is True
    assert columns.at_least("Draft", "Ready") is False
    assert columns.at_least("Refinement", "Ready") is False


def test_a_column_the_project_does_not_offer_is_never_past_anything():
    """An unread column and a column a person invented both mean the same here: nothing is known."""
    assert columns.at_least(None, "Ready") is False
    assert columns.at_least("Blocked", "Ready") is False
