from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import (
    answers,
    asks,
    chores,
    decisions,
    features,
    implplan,
    planning,
    review,
    spec,
    stalls,
    versions,
    watch,
)
from deckhand.credentials import Credentials
from deckhand.features import FEATURE_LABEL
from deckhand.ops import create_id
from deckhand.records import HAND, Record
from deckhand.step import Refusal
from deckhand.steps.after import After
from deckhand.steps.decide import Decide
from deckhand.store import open as store_open
from deckhand.store.fake import FakeStore, WriteFailed
from deckhand.store.local import default_path
from deckhand.store.state import LocalState
from tests.test_feature_approval import approve_everything, mapped, reviewed, setup
from tests.test_implplan import manifest
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
    store = approved_ready()
    store.create_issue("x1", "ACME", "Elsewhere", "", None, [])
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    version = versions.version_for(store, feature, "feature-approval", P1)
    step = Decide(store)
    step.context(["feature-approval", P1, *TEAM, "--json"])
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
        [record] = [r for r in store.records(story) if r.kind == "feature-approval"]
        assert (record.version, record.author) == (version, "person:sess")
        assert record.data["by"] == "viewer-1"
    assert store.records("x1") == []
    assert not decisions.pending_path(nonce, state).exists()


def test_story_decision_pins_the_story_by_reference(state: Path, capsys):
    store = feature_store()
    Decide(store).context(["lean", "ACME-2", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    record_answer(state, shown["tool_input"]["questions"][0], "Prove it in Lean")
    Decide(store).apply([shown["nonce"], *TEAM])
    [record] = store.records("s2")
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    assert (record.kind, record.subject) == ("lean", "s2")
    assert record.version == versions.version_for(store, feature, "lean", "s2")
    assert store.records("s1") == []


def mapped_item(store: FakeStore, draft: str = "'Bonjour'", stories: tuple[str, ...] = ("s1",)) -> None:
    data = {"unknowns": [], "new_here": [], "decisions": [{"item": "Greeting wording", "draft": draft}]}
    for story in stories:
        store.put_record(story, Record("stub", "greeting", "1", versions.MAP_AUTHOR, data))


def ask_item(store: FakeStore, capsys, answer: str, state: Path) -> None:
    Decide(store).context(["decision-item", "Greeting wording", "--story", "ACME-1", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    asked = shown["tool_input"]["questions"][0]
    assert asked["question"].startswith("Greeting wording: 'Bonjour'? [")
    assert [o["label"] for o in asked["options"]] == ["Yes", "Change"]
    record_answer(state, asked, answer)
    Decide(store).apply([shown["nonce"], *TEAM])


def test_yes_records_the_draft_with_a_version_that_covers_it(state: Path, capsys):
    store = feature_store()
    mapped_item(store)
    ask_item(store, capsys, "Yes", state)
    [record] = [r for r in store.records("s1") if r.kind == "decision-item"]
    assert (record.subject, record.version) == (
        versions.item_subject("Greeting wording"),
        versions.item_version("Greeting wording", "'Bonjour'"),
    )
    assert (record.data["answer"], record.data["item"]) == ("'Bonjour'", "Greeting wording")


def test_own_words_are_what_the_hand_receives(state: Path, capsys):
    store = feature_store()
    mapped_item(store)
    ask_item(store, capsys, "'Salut'", state)
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    found = spec.gather(store, feature, features.story(store, feature, "ACME-1"))
    assert found.decisions == [{"item": "Greeting wording", "answer": "'Salut'", "note": None}]
    assert "- Greeting wording: 'Salut'" in spec.text(found)
    assert "Change" not in spec.text(found)


def test_yes_hands_the_draft_to_the_hand(state: Path, capsys):
    store = feature_store()
    mapped_item(store)
    ask_item(store, capsys, "Yes", state)
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    shown = spec.text(spec.gather(store, feature, features.story(store, feature, "ACME-1")))
    assert "# Decisions\n\n- Greeting wording: 'Bonjour'\n" in shown


def test_approval_drops_the_hold(state: Path, capsys):
    store = approved_ready()
    with LocalState(default_path()) as rows:
        rows.put(watch.HOLD, {})
    asked_approval(store, capsys, state, "Change")
    with LocalState(default_path()) as rows:
        assert rows.get(watch.HOLD) is not None
    asked_approval(store, capsys, state, "Approve")
    with LocalState(default_path()) as rows:
        assert rows.get(watch.HOLD) is None


def approved_ready() -> FakeStore:
    store = setup()
    for story in ("s1", "s2"):
        mapped(store, story)
    reviewed(store)
    return store


def asked_approval(store: FakeStore, capsys, state: Path, answer: str) -> None:
    Decide(store).context(["feature-approval", P1, *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    asked = shown["tool_input"]["questions"][0]
    assert [o["label"] for o in asked["options"]] == ["Approve", "Change"]
    record_answer(state, asked, answer)
    Decide(store).apply([shown["nonce"], *TEAM])
    capsys.readouterr()


def test_a_bare_change_records_nothing_and_the_item_is_asked_again(state: Path, capsys):
    store = feature_store()
    mapped_item(store)
    store.writes.clear()
    ask_item(store, capsys, "Change", state)
    assert store.writes == []
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    assert features.approval_gaps(store, feature)[-1:] != []
    assert "ACME-1: decision item Greeting wording is not decided" in features.approval_gaps(store, feature)


def test_a_changed_draft_is_asked_again(state: Path, capsys):
    store = feature_store()
    mapped_item(store)
    ask_item(store, capsys, "Yes", state)
    gap = "ACME-1: decision item Greeting wording is not decided"
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    assert gap not in features.approval_gaps(store, feature)
    mapped_item(store, "'Salut'")
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    assert gap in features.approval_gaps(store, feature)


def test_an_item_outside_the_map_or_with_two_drafts_is_refused(state: Path):
    store = feature_store()
    mapped_item(store)
    with pytest.raises(Refusal, match="not a decision item of the story map"):
        Decide(store).context(["decision-item", "Other", "--story", "ACME-1", *TEAM])
    mapped_item(store, "'Salut'", ("s2",))
    with pytest.raises(Refusal, match="one draft for every --story"):
        Decide(store).context(["decision-item", "Greeting wording", "--story", "ACME-1", "--story", "ACME-2", *TEAM])


@pytest.mark.parametrize(
    "args,match",
    [
        (["feature-approval", "p9"], "p9 is not the open feature"),
        (["feature-approval", P1, "--story", "s1"], "--story applies only to decision-item"),
        (["lean", "ACME-3"], "ACME-3 is not a story of Alpha"),
        (["decision-item", "Keep it"], "decision-item needs at least one --story"),
        (["feature-approval", P1, "--version", "v1"], "unknown argument --version"),
    ],
)
def test_context_refuses_what_it_cannot_pin(state: Path, args: list[str], match: str):
    with pytest.raises(Refusal, match=match):
        Decide(feature_store()).context([*args, *TEAM])


def test_apply_without_pending_refuses(state: Path):
    with pytest.raises(Refusal, match="no pending question"):
        Decide(FakeStore()).apply(["deadbeef", *TEAM])


def test_questions_show_identifiers_titles_and_the_feature_name(state: Path, capsys):
    store = approved_ready()
    Decide(store).context(["feature-approval", P1, *TEAM, "--json"])
    text = json.loads(capsys.readouterr().out)["tool_input"]["questions"][0]["question"]
    assert "Alpha" in text and "ACME-1 Orders, ACME-2 Receipts" in text
    assert P1 not in text and "s1" not in text
    Decide(store).context(["lean", "ACME-2", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)["tool_input"]["questions"][0]
    assert "ACME-2 Receipts" in shown["question"] and "s2" not in shown["question"]
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
        Decide().context(["lean", "ACME-1", *TEAM])
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
        Decide(store).context(["lean", "ACME-1", *TEAM])
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
    [record] = [r for r in store.records("s1") if r.kind == stalls.STALLED]
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


def test_decide_apply_makes_no_linear_write_for_a_mirror(state: Path, capsys):
    store = approved_ready()
    Decide(store).context(["feature-approval", P1, *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    record_answer(state, shown["tool_input"]["questions"][0], "Approve")
    store.writes.clear()
    Decide(store).apply([shown["nonce"], *TEAM])
    assert store.writes == [("put_record", s, "feature-approval", P1) for s in ("s1", "s2")]
    for story in ("s1", "s2"):
        [record] = [r for r in store.records(story) if r.kind == "feature-approval"]
        assert (record.data["by"], record.data["name"]) == ("viewer-1", "viewer-1")


def test_make_it_a_story_opens_a_parked_feature_named_for_the_chore(state: Path, capsys):
    text = "Run every test under tests/ on main"
    store = setup()
    approve_everything(store)
    store.put_record("s1", Record(implplan.PURPOSE, "s1", "p1", "agent:implplan", manifest(after_merge=[text])))
    store.set_issue_state("s1", "Done")
    store.assign("s1", store.viewer()["id"])
    After(store).apply(["ACME-1", "person", "1", "--why", "it needs main", *TEAM])
    before = features.open_feature(store, "ACME")
    assert before is not None
    version = versions.feature_version(store, before)
    capsys.readouterr()
    Decide(store).context(["after-merge", "ACME-1", "--item", "1", *TEAM, "--json"])
    asked = json.loads(capsys.readouterr().out)
    question = asked["tool_input"]["questions"][0]
    assert question["question"].startswith("What happens to this after-merge item of ACME-1 Orders")
    record_answer(state, question, "Make it a story")
    store.fail_after_write(len(store.writes) + 1)
    with pytest.raises(WriteFailed):
        Decide(store).apply([asked["nonce"], *TEAM])
    Decide(store).apply([asked["nonce"], *TEAM])
    id = create_id("ACME:chore:s1:1", features.PURPOSE)
    assert store.writes.count(("create_project", id)) == 1
    made = store.project(id)
    assert made is not None
    assert (made["name"], made["start_date"], made["target_date"]) == (f"Chore: {text}", None, None)
    assert made["labels"] == [FEATURE_LABEL]
    assert all(part in made["description"] for part in (text, "ACME-1", "it needs main"))
    [decided] = [r for r in store.records("s1") if r.kind == chores.AFTER]
    assert (decided.data["answer"], decided.data["project"]) == ("Make it a story", id)
    after = features.open_feature(store, "ACME")
    assert after is not None and after.project["id"] == P1
    assert versions.feature_version(store, after) == version
    assert [p["id"] for p in features.parked(store, "ACME")] == [id]
