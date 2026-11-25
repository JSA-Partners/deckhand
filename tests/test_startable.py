from __future__ import annotations

from pathlib import Path

from deckhand import config, features, order, startable
from tests.test_feature_approval import P1, mapped
from tests.test_reconcile import started
from tests.test_step_hand import ME, building, clone

__all__ = ["clone"]


def test_a_story_added_mid_build_waits_for_the_approval(clone: Path):
    store = building()
    started(store, "s1")
    store.create_issue("s3", "ACME", "Refunds", "Outcome: a refund is paid.", P1, ["widgets"])
    mapped(store, "s3")
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    assert (features.phase(feature), feature.reapproval_due) == (features.Phase.BUILDING, True)
    clones = config.local().clones
    assert [w.story["identifier"] for w in startable.startable(store, feature, ME, clones)] == ["ACME-1"]
    edges = order.edges(store, feature)
    added = next(s for s in feature.live if s["identifier"] == "ACME-3")
    assert startable.why_not(store, feature, added, ME, edges, clones) == "ACME-3 waits for the changed plan's approval"
