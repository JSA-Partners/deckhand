from __future__ import annotations

from dataclasses import replace

from deckhand import chores, features, implplan, mirror, ops, planning, signoff, spikes, versions
from deckhand.actions import Attach, PutDocument
from deckhand.records import Record
from deckhand.store.fake import DOCUMENT_URL, FakeStore
from tests.test_feature_approval import P1, decided, load, reapprove
from tests.test_step_hand import ME, building

CONTRACT = f"{DOCUMENT_URL}{ops.create_id(P1, 'section:Contract')}"
COMMIT = "https://github.com/acme/widgets/commit/c2"


def named(store: FakeStore, name: str = "Ada") -> FakeStore:
    for story in ("s1", "s2"):
        [held] = [r for r in store.records(story) if r.kind == features.APPROVAL]
        store.put_record(story, replace(held, data=held.data | {"name": name}))
    return store


def signed(store: FakeStore, answer: str = signoff.SIGN) -> Record:
    data = {"answer": answer, "by": ME, "name": "Ada", "repo": "acme/widgets", "nonce": "n1"}
    store.put_record("s1", Record(signoff.KIND, "s1", "c2:digest", "person:sess", data))
    [held] = [r for r in store.records("s1") if r.kind == signoff.KIND]
    return held


def attachments(store: FakeStore) -> list[Attach]:
    return [a for a in mirror.wanted(store, load(store)) if isinstance(a, Attach)]


def test_an_approval_wants_one_attachment_per_live_story_at_the_contract_url():
    store = named(building())
    found = attachments(store)
    assert [(a.name, a.key, a.issue, a.url) for a in found] == [
        ("mirror:s1", f"mirror:s1:{CONTRACT}", "s1", CONTRACT),
        ("mirror:s2", f"mirror:s2:{CONTRACT}", "s2", CONTRACT),
    ]
    [held] = [r for r in store.records("s1") if r.kind == features.APPROVAL]
    assert (found[0].title, found[0].subtitle) == ("Plan approved by Ada", "{approvedAt__since}")
    assert dict(found[0].metadata) == {"version": held.version, "approvedBy": "Ada", "approvedAt": held.at}


def test_an_approval_without_a_name_wants_nothing():
    assert attachments(building()) == []


def test_a_second_approval_rewrites_the_same_url():
    store = named(building())
    [before, _] = attachments(store)
    planning.save_section(store, load(store), "Contract", "POST /orders, then GET /orders.", False)
    reapprove(store)
    named(store)
    [after, _] = attachments(store)
    assert after.key == before.key
    assert dict(after.metadata)["version"] != dict(before.metadata)["version"]
    assert after.digest != before.digest


def test_sign_off_wants_the_commit_url_and_not_yet_wants_none():
    store = building()
    held = signed(store)
    [found] = attachments(store)
    assert (found.name, found.key, found.issue, found.url) == ("mirror:s1", f"mirror:s1:{COMMIT}", "s1", COMMIT)
    assert (found.title, found.subtitle) == ("Signed off by Ada", "{signedAt__since}")
    assert dict(found.metadata) == {"sha": "c2", "signedBy": "Ada", "signedAt": held.at}
    signed(store, signoff.NOT_YET)
    assert attachments(store) == []


def test_a_void_resends_every_key_with_void():
    store = building()
    held = signed(store)
    [before] = attachments(store)
    store.put_record("s1", signoff.void_record("s1", held, "the branch moved past the signed head"))
    [void] = [r for r in store.records("s1") if r.kind == signoff.VOID]
    [found] = attachments(store)
    assert (found.key, found.url) == (before.key, COMMIT)
    assert (found.title, found.subtitle) == ("Sign-off by Ada voided", "the branch moved past the signed head")
    assert dict(found.metadata) == {"sha": "c2", "signedBy": "Ada", "signedAt": held.at, "void": void.at}
    assert found.digest != before.digest


def test_written_mirrors_are_not_due_again():
    store = named(building())
    signed(store)
    feature = load(store)
    wanted = mirror.wanted(store, feature)
    assert len(wanted) == 3
    assert mirror.due(store, feature, {}) == wanted
    assert mirror.due(store, feature, {a.key: a.digest for a in wanted}) == []


def mapped_stub(store: FakeStore, story: str, decisions: list[dict], after: list[str]) -> None:
    data = {"unknowns": [], "new_here": [], "decisions": decisions, "after_merge": after}
    store.put_record(story, Record("stub", story, "1", versions.MAP_AUTHOR, data))


def test_the_story_map_body_lists_tier_answers_and_chores():
    store = building()
    mapped_stub(store, "s1", [{"item": "Greeting wording", "draft": "Yes"}], ["Run the backfill"])
    mapped_stub(store, "s2", [], [])
    decided(store, "decision-item", "Greeting wording", "'Bonjour'", ["s1"])
    plan = {"after_merge": ["Run the backfill", "Rotate the key"]}
    store.put_record("s1", Record(implplan.PURPOSE, "s1", "p1", "agent:hand", plan))
    store.put_record("s1", chores.after_record(chores.AFTER_DONE, "s1", 1, "Run the backfill", {"note": "ran"}))
    feature = load(store)
    [document] = [a for a in mirror.wanted(store, feature) if isinstance(a, PutDocument)]
    assert (document.name, document.key) == (f"mirror:{P1}", f"mirror:{P1}:story-map")
    assert (document.id, document.parent, document.title) == (ops.create_id(P1, "story-map"), P1, "Story map")
    assert document.body == (
        "## ACME-1 Orders\n\n"
        "Tier: None of these\n\n"
        "- Greeting wording: 'Bonjour'\n"
        "- After merge: Run the backfill (done by the hand)\n"
        "- After merge: Rotate the key (open)\n\n"
        "## ACME-2 Receipts\n\n"
        "Tier: None of these\n"
    )
    before = (feature.version, spikes.answered(store, feature), planning.next_section(store, feature))
    store.put_document(document.id, document.parent, document.title, document.body)
    after = load(store)
    assert (after.version, spikes.answered(store, after), planning.next_section(store, after)) == before


def test_no_story_map_is_wanted_without_local_map_records():
    store = building()
    mapped_stub(store, "s1", [], [])
    assert not [a for a in mirror.wanted(store, load(store)) if isinstance(a, PutDocument)]
    for story in ("s1", "s2"):
        store.set_issue_state(story, "Done")
    released = features.current(store, "ACME")
    assert released is not None and features.phase(released) is features.Phase.RELEASED
    assert mirror.wanted(store, replace(released, records={})) == []


def test_before_a_plan_the_story_map_lists_the_maps_after_merge_items_as_open():
    store = building()
    mapped_stub(store, "s1", [], ["Run the backfill"])
    mapped_stub(store, "s2", [], [])
    [document] = [a for a in mirror.wanted(store, load(store)) if isinstance(a, PutDocument)]
    assert "- After merge: Run the backfill (open)\n" in document.body
