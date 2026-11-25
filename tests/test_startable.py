from __future__ import annotations

from pathlib import Path

from deckhand import config, features, order, queue, startable
from deckhand.records import Record
from tests.test_feature_approval import P1, mapped
from tests.test_reconcile import started
from tests.test_step_hand import ME, building, clone

__all__ = ["clone"]


def test_a_story_added_mid_build_is_drafted_then_waits_for_the_approval(clone: Path):
    store = building()
    store.assign("s2", "someone-else")
    started(store, "s1")
    store.create_issue("s3", "ACME", "Refunds", "Outcome: a refund is paid.", P1, ["widgets"])
    mapped(store, "s3")
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    assert (features.phase(feature), feature.reapproval_due) == (features.Phase.BUILDING, True)
    clones = config.local().clones
    found = [(w.story["identifier"], w.kind) for w in startable.startable(store, feature, ME, clones)]
    assert found == [("ACME-1", startable.BUILD), ("ACME-3", startable.DRAFT)]
    store.put_record("s3", Record(queue.TAKEN, "s3", "d1", "agent:captain", {}))
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    edges = order.edges(store, feature)
    added = next(s for s in feature.live if s["identifier"] == "ACME-3")
    assert startable.why_not(store, feature, added, ME, edges, clones) == "ACME-3 waits for the changed plan's approval"
