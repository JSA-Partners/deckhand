from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import answers, asks, decisions, features, planning, queue, review, versions
from deckhand.credentials import Credentials
from deckhand.features import FEATURE_LABEL
from deckhand.ops import create_id
from deckhand.records import HAND, Record, content_hash
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.store import open as store_open
from deckhand.store.fake import FakeStore
from tests.test_queue import alpha, started

P1 = create_id("ACME:Alpha", "feature")

TEAM = ["--team-key", "ACME"]


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def feature_store() -> FakeStore:
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    store.create_issue("s1", "ACME", "One", "Outcome.", P1, [])
    store.create_issue("s2", "ACME", "Two", "Outcome.", P1, [])
    store.create_issue("x1", "ACME", "Elsewhere", "", None, [])
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    planning.save_section(store, feature, "Contract", "POST /orders.", False)
    return store


def released(store: FakeStore) -> FakeStore:
    for story in ("s1", "s2"):
        store.set_issue_state(story, "Done")
    beta = create_id("ACME:Beta", "feature")
    store.create_project(beta, "ACME", "Beta", "", [FEATURE_LABEL])
    store.set_project_dates(beta, "2026-11-23", "2026-11-30")
    store.create_issue("s3", "ACME", "Three", "Outcome.", beta, [])
    return store


def record_answer(state: Path, asked: dict, answer: str) -> None:
    answers.record(
        {
            "session_id": "sess",
            "tool_response": {
                "questions": [asked],
                "answers": {asked["question"]: answer},
            },
        },
        state,
    )


def test_pending_question_round_trips_through_context_and_apply(state: Path, capsys):
    store = feature_store()
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    version = versions.version_for(store, feature, "contract", P1)
    step = Decide(store)
    step.context(["contract", P1, *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    nonce = shown["nonce"]
    asked = shown["tool_input"]["questions"][0]
    assert asked["header"] == f"dh {nonce}"
    answers.record(
        {
            "session_id": "sess",
            "tool_response": {
                "questions": [asked],
                "answers": {asked["question"]: "Approve"},
            },
        },
        state,
    )
    step.apply([nonce, *TEAM])
    for story in ("s1", "s2"):
        [record] = store.records(story)
        assert (record.kind, record.version, record.author) == ("contract", version, "person:sess")
        assert record.data["by"] == "viewer-1"
    assert store.records("x1") == []
    assert not decisions.pending_path(nonce, state).exists()


def test_story_decision_pins_the_story_by_reference(state: Path, capsys):
    store = feature_store()
    Decide(store).context(["sitting", "ACME-2", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    record_answer(state, shown["tool_input"]["questions"][0], "Done")
    Decide(store).apply([shown["nonce"], *TEAM])
    [record] = store.records("s2")
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    assert (record.kind, record.subject) == ("sitting", "s2")
    assert record.version == versions.version_for(store, feature, "sitting", "s2")
    assert store.records("s1") == []


def test_decision_item_shows_its_text_and_records_an_open_answer(state: Path, capsys):
    store = feature_store()
    text = "Keep orders in one table"
    Decide(store).context(["decision-item", text, "--story", "ACME-1", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    asked = shown["tool_input"]["questions"][0]
    assert text in asked["question"]
    record_answer(state, asked, "Change")
    Decide(store).apply([shown["nonce"], *TEAM])
    [record] = store.records("s1")
    assert (record.subject, record.version) == (versions.item_subject(text), content_hash(text))
    assert (record.data["answer"], record.data["item"]) == ("Change", text)


@pytest.mark.parametrize(
    "args,match",
    [
        (["contract", "p9"], "p9 is not the open feature"),
        (["contract", P1, "--story", "s1"], "--story applies only to decision-item"),
        (["tier", "ACME-3"], "ACME-3 is not a story of Alpha"),
        (["decision-item", "Keep it"], "decision-item needs at least one --story"),
        (["contract", P1, "--version", "v1"], "unknown argument --version"),
    ],
)
def test_context_refuses_what_it_cannot_pin(state: Path, args: list[str], match: str):
    with pytest.raises(Refusal, match=match):
        Decide(feature_store()).context([*args, *TEAM])


def test_apply_without_pending_refuses(state: Path):
    with pytest.raises(Refusal, match="no pending question"):
        Decide(FakeStore()).apply(["deadbeef", *TEAM])


def test_questions_show_identifiers_titles_and_the_feature_name(state: Path, capsys):
    store = feature_store()
    Decide(store).context(["contract", P1, *TEAM, "--json"])
    text = json.loads(capsys.readouterr().out)["tool_input"]["questions"][0]["question"]
    assert "Alpha" in text and "ACME-1 One, ACME-2 Two" in text
    assert P1 not in text and "s1" not in text
    Decide(store).context(["sitting", "ACME-2", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)["tool_input"]["questions"][0]
    assert "ACME-2 Two" in shown["question"] and "s2" not in shown["question"]
    assert all("s2" not in option["description"] for option in shown["options"])


class Connected(Exception):
    pass


def roles_connected(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    roles: list[str] = []

    def connect(credentials: object, role: str, meter: object) -> None:
        roles.append(role)
        raise Connected

    monkeypatch.setattr(Credentials, "load", classmethod(lambda cls, path: object()))
    monkeypatch.setattr(store_open, "connect", connect)
    return roles


def test_decide_writes_as_person(state: Path, monkeypatch: pytest.MonkeyPatch):
    roles = roles_connected(monkeypatch)
    with pytest.raises(Connected):
        Decide().context(["contract", P1, *TEAM])
    assert roles == ["person"]


def test_a_question_from_a_released_story_is_decided(state: Path, capsys: pytest.CaptureFixture[str]):
    store = released(feature_store())
    found = asks.parse_question(json.dumps({"question": "Drop it?", "options": ["Yes", "No"], "why": "Done."}))
    store.put_record("s1", asks.question_record("s1", found))
    capsys.readouterr()
    Decide(store).context(["question", asks.question_id("Drop it?"), "--story", "ACME-1", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    assert [o["label"] for o in shown["tool_input"]["questions"][0]["options"]] == ["Yes", "No"]


def test_a_hand_never_opens_a_decision(state: Path, monkeypatch: pytest.MonkeyPatch):
    store = feature_store()
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    with pytest.raises(Refusal, match="a hand never decides"):
        Decide(store).context(["contract", P1, *TEAM])
    assert not (state / decisions.PENDING).exists()


def test_a_stalled_item_asks_through_decide(state: Path, capsys: pytest.CaptureFixture[str]):
    store = alpha()
    started(store, "s1")
    [start] = [r for r in store.records("s1") if r.kind == asks.HAND_START]
    capsys.readouterr()
    Decide(store).context(["stalled", "ACME-1", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    asked = shown["tool_input"]["questions"][0]
    text = "ACME-1: its hand stopped and has written nothing since it last started. Restart it?"
    assert asked["question"].startswith(text + " [")
    assert [o["label"] for o in asked["options"]] == ["Restart", "Leave it stopped"]
    record_answer(state, asked, "Leave it stopped")
    Decide(store).apply([shown["nonce"], *TEAM])
    [record] = [r for r in store.records("s1") if r.kind == queue.STALLED]
    assert (record.subject, record.version, record.author) == ("s1", start.at, "person:sess")
    assert record.data["answer"] == "Leave it stopped"
    with pytest.raises(Refusal, match="already settled"):
        Decide(store).context(["stalled", "ACME-1", *TEAM, "--json"])
    with pytest.raises(Refusal, match="ACME-2 has no hand started"):
        Decide(store).context(["stalled", "ACME-2", *TEAM, "--json"])


@pytest.mark.parametrize("kind", ["stalled", "config-edit"])
def test_a_released_story_is_decided_for_its_stalled_hand_and_its_config_edit(state: Path, capsys, kind: str):
    store = released(feature_store())
    store.put_record("s1", Record(asks.HAND_START, "s1", "v1", HAND, {"kind": "story", "session": "hand"}))
    edit = asks.parse_edit(json.dumps({"path": ".claude/settings.json", "change": "allow npm", "why": "tests"}))
    store.put_record("s1", asks.edit_record("s1", edit))
    [request] = [r for r in store.records("s1") if r.kind == asks.EDIT_REQUEST]
    args = ["--item", request.subject.rpartition(":")[2]] if kind == asks.EDIT else []
    answer = "Done" if kind == asks.EDIT else "Restart"
    capsys.readouterr()
    Decide(store).context([kind, "ACME-1", *args, *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    record_answer(state, shown["tool_input"]["questions"][0], answer)
    Decide(store).apply([shown["nonce"], *TEAM])
    [record] = [r for r in store.records("s1") if r.kind == kind]
    assert record.data["answer"] == answer


def test_a_branch_cap_question_needs_no_worktree_head(state: Path, capsys: pytest.CaptureFixture[str]):
    store = feature_store()
    p1 = {"lens": "correctness", "severity": "P1", "path": "a.py", "line": 1, "summary": "A hole."}
    found = review.parse_findings(json.dumps([p1 | {"fix": "Fill it.", "evidence": "a.py:1", "confidence": 0.9}]))
    for n in (1, 2, 3):
        plan = review.RoundPlan(n, scoped=n > 1, base_hash="")
        store.put_record("s1", review.round_record("s1", "branch", plan, f"hash{n}", found, f"c{n}"))
    latest = review.last(review.rounds_of(store.records("s1"), "s1:branch"))
    Decide(store).context(["review-cap", "ACME-1", "--stage", "branch", *TEAM, "--json"])
    nonce = json.loads(capsys.readouterr().out)["nonce"]
    assert decisions.load_pending(nonce, answers.state_dir()).version == review.cap_version(latest)


def test_review_cap_context_takes_the_feature_stage(state: Path, capsys: pytest.CaptureFixture[str]):
    store = feature_store()
    p1 = {"lens": "correctness", "severity": "P1", "path": "Contract", "line": 1, "summary": "A hole."}
    found = review.parse_findings(json.dumps([p1 | {"fix": "Fill it.", "evidence": "Contract", "confidence": 0.9}]))
    for n in (1, 2, 3):
        plan = review.RoundPlan(n, scoped=n > 1, base_hash="")
        for story in ("s1", "s2"):
            store.put_record(story, review.round_record(P1, "feature", plan, f"hash{n}", found))
    Decide(store).context(["review-cap", P1, "--stage", "feature", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    asked = shown["tool_input"]["questions"][0]
    assert asked["question"].startswith("Three review rounds left a P1 open. Proceed to approval or rework the feature")
    assert "Alpha: 3 rounds left a P1 open" in asked["question"]
    assert [o["label"] for o in asked["options"]] == [review.PROCEED, review.REWORK]
    record_answer(state, asked, review.REWORK)
    Decide(store).apply([shown["nonce"], *TEAM])
    for story in ("s1", "s2"):
        [record] = [r for r in store.records(story) if r.kind == review.CAP_DECISION]
        assert record.data["answer"] == review.REWORK
    assert review.counted(store.records("s1"), f"{P1}:feature") == ([], 3)
