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
