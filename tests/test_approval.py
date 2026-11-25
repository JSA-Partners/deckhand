from __future__ import annotations

from deckhand import approval, planning, queue, versions
from deckhand.records import Record
from tests.test_feature_approval import decided, load, mapped, setup

GREETING = {"item": "Greeting wording", "draft": "Yes"}
RECEIPTS = {"item": "Send receipts", "draft": "By email"}


def test_approval_screen_holds_every_part():
    store = setup()
    planning.save_section(store, load(store), "Contract", "POST /orders keeps a\\_b.", False)
    store.set_issue_text("s1", "Orders", "Outcome: an order is placed, see [x.io](<http://x.io>).")
    store.relate("s1", "s2")
    mapped(store, "s1", "multi_step", "placed -> paid")
    mapped(store, "s2")
    stub = {"unknowns": [], "new_here": [], "decisions": [GREETING], "after_merge": ["drain the queue"]}
    store.put_record("s1", Record("stub", "orders", "1", versions.MAP_AUTHOR, stub))
    stub = {"unknowns": [], "new_here": [], "decisions": [RECEIPTS], "after_merge": []}
    store.put_record("s2", Record("stub", "receipts", "1", versions.MAP_AUTHOR, stub))
    decided(store, "decision-item", "Greeting wording", "Hello", ["s1"])

    assert approval.screen(store, load(store)) == (
        "# Contract\n\nWritten by the person.\n\nPOST /orders keeps a_b.\n\n"
        "# Examples\n\nWritten by the person.\n\nThe Examples of Alpha.\n\n"
        "# Invariants\n\nWritten by the person.\n\nThe Invariants of Alpha.\n\n"
        "# Story map\n\n"
        "## ACME-1 Orders\n"
        "repository: widgets\n"
        "depends on: none\n"
        "tier: A flow or an entity with several states\n\n"
        "Outcome: an order is placed, see x.io.\n\n"
        "decisions:\n- Greeting wording: Hello\n"
        "state chart:\nplaced -> paid\n"
        "after merge:\n- drain the queue\n\n"
        "## ACME-2 Receipts\n"
        "repository: widgets\n"
        "depends on: ACME-1\n"
        "tier: None of these\n\n"
        "Outcome: a receipt is sent.\n\n"
        "decisions:\n- Send receipts: By email (not decided)\n"
        "state chart: none\n"
        "after merge: none\n"
    )


def test_a_story_without_a_map_says_so():
    store = setup()
    screen = approval.screen(store, load(store))
    assert "## ACME-1 Orders\nrepository: widgets\ndepends on: none\ntier: not given\n" in screen
    assert "decisions: none\nstate chart: none\nafter merge: none\n" in screen


def test_a_draft_left_untaken_is_named():
    store = setup()
    store.put_record("s2", Record(queue.DRAFT, "s2", "v1", "agent:draft", {}))
    screen = approval.screen(store, load(store))
    assert screen.count("draft: saved, not taken\n") == 1
    assert "Outcome: a receipt is sent.\n\ndraft: saved, not taken\ndecisions: none\n" in screen
