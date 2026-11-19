from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import claims
from deckhand.step import Refusal
from deckhand.steps.implplan import ImplPlan
from deckhand.store.fake import FakeStore
from tests.test_implplan import BASE, Git, manifest
from tests.test_step_review import PLAN, TEAM, planned, repo

__all__ = ["repo"]


def store_with_story() -> FakeStore:
    store = FakeStore(viewer="ann")
    store.create_issue("s1", "ACME", "One", "", None, [])
    store.writes.clear()
    return store


def test_me_is_the_viewer_of_the_given_store():
    assert claims.me(FakeStore(viewer="ann")) == "ann"


def test_claim_is_idempotent():
    store = store_with_story()
    claims.claim(store, store.issue("s1"), "ann")
    claims.claim(store, store.issue("s1"), "ann")
    assert store.writes == [("assign", "s1", "ann")]
    assert store.issue("s1")["assignee"] == "ann"


def test_claim_refuses_taken_story():
    store = store_with_story()
    store.assign("s1", "bob")
    store.writes.clear()
    with pytest.raises(Refusal, match="^ACME-1 belongs to bob$"):
        claims.claim(store, store.issue("s1"), "ann")
    assert store.writes == []


def test_owned_passes_the_owner_and_an_unclaimed_story():
    store = store_with_story()
    claims.owned(store.issue("s1"), "ann")
    store.assign("s1", "ann")
    claims.owned(store.issue("s1"), "ann")
    with pytest.raises(Refusal, match="^ACME-1 belongs to ann$"):
        claims.owned(store.issue("s1"), "bob")


def test_race_loser_next_write_refuses(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    seen = store.issue("s1")
    claims.claim(store, seen, "ann")
    claims.claim(store, seen, "bob")
    assert store.issue("s1")["assignee"] == "bob"
    plan, found = tmp_path / "again.md", tmp_path / "again.json"
    plan.write_text(PLAN + "- [ ] more\n")
    found.write_text(json.dumps(manifest()))
    store.writes.clear()
    with pytest.raises(Refusal, match="^ACME-1 belongs to bob$"):
        ImplPlan(store, Git(BASE), repo, me=lambda: "ann").apply(
            ["s1", *TEAM, "--plan", str(plan), "--manifest", str(found)]
        )
    assert store.writes == []
    ImplPlan(store, Git(BASE), repo, me=lambda: "bob").apply(
        ["s1", *TEAM, "--plan", str(plan), "--manifest", str(found)]
    )
    assert store.writes
