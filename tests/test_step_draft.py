from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from deckhand import features, launch, ops, planning, queue, spec
from deckhand.step import Refusal
from deckhand.steps.draft import Draft
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import P1, approve_everything, decided, setup

TEAM = ["--team-key", "ACME"]
DRAFT = "Outcome: an order is placed.\n\nExamples:\n- a paid order ships.\n"


def draft_file(tmp_path: Path, text: str = DRAFT) -> str:
    path = tmp_path / "draft.md"
    path.write_text(text)
    return str(path)


def approved_contract() -> FakeStore:
    store = setup()
    decided(store, "contract", P1, "Approve", ["s1", "s2"])
    return store


def test_draft_refused_before_contract(tmp_path: Path):
    store = setup()
    written = list(store.writes)
    with pytest.raises(Refusal, match="Contract is not approved"):
        Draft(store, me=lambda: "viewer-1").apply(["ACME-1", "--file", draft_file(tmp_path), *TEAM])
    assert store.writes == written


def test_draft_saves_a_document_and_a_record(tmp_path: Path):
    store = approved_contract()
    before = len(store.writes)
    Draft(store, me=lambda: "viewer-1").apply(["ACME-1", "--file", draft_file(tmp_path), *TEAM])
    assert store.writes[before:] == [
        ("put_document", ops.create_id("s1", "draft")),
        ("put_record", "s1", queue.DRAFT, "s1"),
    ]
    document = store.document(ops.create_id("s1", "draft"))
    assert document is not None
    assert (document["title"], document["body"]) == ("Draft", DRAFT)
    [record] = [r for r in store.records("s1") if r.kind == queue.DRAFT]
    assert record.subject == "s1"
    assert store.issue("s1")["description"] == "Outcome: an order is placed."


def test_draft_rerun_writes_nothing(tmp_path: Path):
    store = approved_contract()
    path = draft_file(tmp_path)
    Draft(store, me=lambda: "viewer-1").apply(["ACME-1", "--file", path, *TEAM])
    written = list(store.writes)
    Draft(store, me=lambda: "viewer-1").apply(["ACME-1", "--file", path, *TEAM])
    assert store.writes == written


def test_draft_refuses_another_persons_story(tmp_path: Path):
    store = approved_contract()
    store.assign("s1", "someone-else")
    written = list(store.writes)
    with pytest.raises(Refusal, match="ACME-1 belongs to someone-else"):
        Draft(store, me=lambda: "viewer-1").apply(["ACME-1", "--file", draft_file(tmp_path), *TEAM])
    assert store.writes == written


def test_draft_refused_once_the_feature_is_building(tmp_path: Path):
    store = setup()
    approve_everything(store)
    written = list(store.writes)
    with pytest.raises(Refusal, match="not planning"):
        Draft(store, me=lambda: "viewer-1").apply(["ACME-1", "--file", draft_file(tmp_path), *TEAM])
    assert store.writes == written


def test_draft_taken_writes_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-7")
    store = approved_contract()
    step = Draft(store, me=lambda: "viewer-1")
    step.apply(["ACME-1", "--file", draft_file(tmp_path), *TEAM])
    before = len(store.writes)
    step.apply(["ACME-1", "take", *TEAM])
    assert [w[:3] for w in store.writes[before:]] == [
        ("set_issue_text", "s1", "Orders"),
        ("put_record", "s1", queue.TAKEN),
    ]
    [taken] = [r for r in store.records("s1") if r.kind == queue.TAKEN]
    assert taken.author == "person:session-7"
    assert store.issue("s1")["description"] == DRAFT
    assert [r.kind for r in store.records("s1") if r.kind == queue.TAKEN] == [queue.TAKEN]
    written = list(store.writes)
    step.apply(["ACME-1", "take", *TEAM])
    assert store.writes == written


def test_take_refuses_without_a_draft():
    store = approved_contract()
    written = list(store.writes)
    with pytest.raises(Refusal, match="ACME-1 has no draft"):
        Draft(store, me=lambda: "viewer-1").apply(["ACME-1", "take", *TEAM])
    assert store.writes == written


def test_context_prints_the_stub_the_contract_and_the_shape(capsys: pytest.CaptureFixture[str]):
    store = approved_contract()
    Draft(store, me=lambda: "viewer-1").context(["ACME-1", *TEAM])
    out = capsys.readouterr().out
    assert "ACME-1 Orders" in out
    assert "Outcome: an order is placed." in out
    for name in ("Frame", "Shape", "Contract", "Examples"):
        assert f"# {name}\n\nWritten by the person.\n\nThe {name} of Alpha.\n" in out
    assert out.index("# Examples") < out.index("never settled here")


def test_draft_refused_once_the_draft_is_taken(tmp_path: Path):
    store = approved_contract()
    step = Draft(store, me=lambda: "viewer-1")
    step.apply(["ACME-1", "--file", draft_file(tmp_path), *TEAM])
    step.apply(["ACME-1", "take", *TEAM])
    written = list(store.writes)
    with pytest.raises(Refusal, match="ACME-1 took its draft already"):
        step.apply(["ACME-1", "--file", draft_file(tmp_path, "Outcome: another.\n"), *TEAM])
    assert store.writes == written


def test_a_long_feature_prints_what_was_approved_and_saves_the_whole_draft_context(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    store = approved_contract()
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    planning.save_section(store, feature, "Examples", "A paid order of two ships.\n" * 1500, True)
    Draft(store, me=lambda: "viewer-1").context(["ACME-1", *TEAM])
    out = capsys.readouterr().out
    saved = tmp_path / launch.INPUT / "ACME-1-draft.md"
    assert len(out.encode("utf-8")) < spec.LIMIT
    assert "The Contract of Alpha." in out and str(saved) in out and "never settled here" not in out
    assert "never settled here" in saved.read_text() and "A paid order of two ships.\n" * 1500 in saved.read_text()
