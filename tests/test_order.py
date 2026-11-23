from __future__ import annotations

from deckhand.features import Feature
from deckhand.order import build_order, edges, ready, unblocks
from deckhand.store.fake import FakeStore


def story(id: str, number: int, state: str = "Backlog") -> dict:
    return {"id": id, "identifier": f"ACME-{number}", "number": number, "state": state}


def project() -> dict:
    return {"id": "p", "name": "Feature", "description": "", "start_date": "2026-11-16", "target_date": None}


def test_edges_keeps_blocks_between_live_stories():
    store = FakeStore()
    store.relate("a", "b")
    store.relate("b", "gone")
    feature = Feature(project(), [story("a", 1), story("b", 2), story("gone", 3, "Canceled")])
    assert edges(store, feature) == [("a", "b")]


def test_order_respects_blockers():
    stories = [story("a", 3), story("b", 1), story("c", 2)]
    ordered, cycle = build_order(stories, [("a", "c"), ("c", "b")])
    assert [s["id"] for s in ordered] == ["a", "c", "b"]
    assert cycle == []


def test_ties_by_number():
    stories = [story("x", 5), story("y", 2), story("z", 9)]
    ordered, cycle = build_order(stories, [])
    assert [s["id"] for s in ordered] == ["y", "x", "z"]
    assert cycle == []


def test_cycle_returned_not_raised():
    stories = [story("a", 1), story("b", 2), story("c", 3), story("d", 4)]
    ordered, cycle = build_order(stories, [("a", "b"), ("b", "a"), ("a", "d")])
    assert sorted(cycle) == ["ACME-1", "ACME-2"]
    assert sorted(s["id"] for s in ordered) == ["a", "b", "c", "d"]


def test_unblocks_counts_transitively():
    blocks = [("a", "b"), ("b", "c"), ("a", "d")]
    assert unblocks("a", blocks, {"b", "c", "d"}) == 3
    assert unblocks("a", blocks, {"c", "d"}) == 1
    assert unblocks("b", blocks, {"b", "c", "d"}) == 1


def test_unblocks_stops_at_a_closed_story():
    blocks = [("a", "b"), ("b", "c"), ("a", "d")]
    assert unblocks("a", blocks, {"a", "c", "d"}) == 1
    assert unblocks("a", blocks, {"a", "b", "c", "d"}) == 3


def test_not_ready_until_blocker_done():
    blocks = [("a", "c"), ("b", "c")]
    by_id = {"a": story("a", 1, "Done"), "b": story("b", 2, "In Progress"), "c": story("c", 3)}
    assert ready(by_id["a"], blocks, by_id) is True
    assert ready(by_id["c"], blocks, by_id) is False
    by_id["b"]["state"] = "Done"
    assert ready(by_id["c"], blocks, by_id) is True
