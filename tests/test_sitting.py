from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from deckhand import features, ops, sitting, versions
from deckhand.ops import create_id
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.sitting import Sitting
from deckhand.steps.stories import Stories
from deckhand.store.fake import FakeStore
from deckhand.tiers import FACTS, tier

P1 = create_id("ACME:Alpha", "feature")

TEAM = ["--team-key", "ACME"]
STORY = {"id": "s1", "title": "Orders", "description": "Outcome: an order is placed."}
START = datetime(2026, 11, 18, 9, 0)


def test_tier_0_story_with_nothing_new_gets_no_questions():
    assert sitting.questions_for(STORY, tier(set()), [], [], []) == []


def test_tier_3_story_gets_failure_questions():
    asked = sitting.questions_for(STORY, tier({"concurrency"}), [], [], [])
    assert asked == [sitting.FAILURE, sitting.RETRY]


def test_tier_1_story_gets_the_data_rule_question():
    assert sitting.questions_for(STORY, tier({"data_rule"}), [], [], []) == [sitting.DATA_RULE]


def test_story_touching_a_contract_gets_the_contract_question():
    assert sitting.questions_for(STORY, tier(set()), [], [], ["orders"]) == [sitting.CONTRACT]
    assert sitting.questions_for(STORY, tier({"crosses_repositories"}), [], [], []) == [sitting.CONTRACT]
    assert sitting.questions_for(STORY, tier(set()), [], [], ["refunds"]) == []


def test_each_unknown_adds_a_question():
    asked = sitting.questions_for(STORY, tier(set()), ["rate limits", "retention"], [], [])
    assert len(asked) == 2
    assert ["rate limits" in q for q in asked] == [True, False]


def test_each_new_here_item_is_its_own_screen():
    asked = sitting.questions_for(STORY, tier(set()), [], ["queue", "cron"], [])
    assert len(asked) == 2
    assert ["queue" in q for q in asked] == [True, False]
    assert ["cron" in q for q in asked] == [False, True]


def feature_with(*ids: str) -> tuple[FakeStore, features.Feature]:
    store = FakeStore()
    store.add_repository_label("ACME", "widgets")
    store.create_project(P1, "ACME", "Alpha", "")
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    for id in ids:
        store.create_issue(id, "ACME", id, "Outcome.", P1, [])
    found = features.open_feature(store, "ACME")
    assert found is not None
    return store, found


def test_progress_notes_the_hour_mark():
    store, feature = feature_with("s1", "s2")
    early = sitting.progress(store, feature, START, START + timedelta(minutes=59))
    assert early == "59 minutes in the sitting, 2 stories left"
    late = sitting.progress(store, feature, START, START + timedelta(minutes=60))
    assert late.startswith("60 minutes in the sitting, 2 stories left")
    assert "an hour" in late


def test_progress_counts_only_stories_without_a_current_sitting():
    store, feature = feature_with("s1", "s2")
    version = versions.version_for(store, feature, "sitting", "s1")
    ops.ensure_record(store, "s1", Record("sitting", "s1", version, "person:s", {"answer": "Done"}))
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    assert sitting.progress(store, feature, START, START).endswith("1 stories left")


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def mapped(tmp_path: Path) -> FakeStore:
    store, _ = feature_with()
    entry = {
        "key": "api",
        "title": "Orders API",
        "outcome": "Orders are placed.",
        "repository": "widgets",
        "depends_on": [],
        "unknowns": ["rate limits"],
        "new_here": ["queue"],
    }
    path = tmp_path / "map.json"
    path.write_text(json.dumps([entry]))
    Stories(store).apply([*TEAM, "--file", str(path)])
    return store


def test_context_prints_tier_questions_and_progress(state: Path, tmp_path: Path, capsys: pytest.CaptureFixture):
    store = mapped(tmp_path)
    id = create_id(P1, "story:api")
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    version = versions.version_for(store, feature, "tier", id)
    answer = next(f.question for f in FACTS if f.key == "concurrency")
    data = {"answer": answer + ", odd", "facts": ["concurrency"]}
    ops.ensure_record(store, id, Record("tier", id, version, "person:s", data))
    clock = iter([START, START + timedelta(minutes=12)])
    step = Sitting(store, lambda: next(clock))
    capsys.readouterr()
    step.context(["ACME-1", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    assert shown["story"] == "ACME-1"
    assert shown["tier"] == 3
    assert shown["questions"][:2] == [sitting.FAILURE, sitting.RETRY]
    assert any("rate limits" in q for q in shown["questions"])
    assert any("queue" in q for q in shown["questions"])
    assert shown["progress"] == "0 minutes in the sitting, 1 stories left"
    step.context(["ACME-1", *TEAM])
    assert "12 minutes in the sitting, 1 stories left" in capsys.readouterr().out


def test_context_refuses_without_a_current_tier(state: Path, tmp_path: Path):
    store = mapped(tmp_path)
    with pytest.raises(Refusal, match="tier of ACME-1 is not decided"):
        Sitting(store, lambda: START).context(["ACME-1", *TEAM])


def test_apply_saves_the_answers_as_the_sitting_document(state: Path, tmp_path: Path):
    store = mapped(tmp_path)
    id = create_id(P1, "story:api")
    path = tmp_path / "answers.md"
    path.write_text("Rate limits: 100 a minute.\n")
    store.writes.clear()
    Sitting(store, lambda: START).apply(["ACME-1", *TEAM, "--file", str(path)])
    Sitting(store, lambda: START).apply(["ACME-1", *TEAM, "--file", str(path)])
    document = create_id(id, "sitting")
    assert store.writes == [("put_document", document)]
    saved = store.document(document)
    assert saved is not None
    assert (saved["parent"], saved["title"], saved["body"]) == (id, "Sitting", "Rate limits: 100 a minute.")


def test_apply_refuses_an_empty_file_and_a_story_outside_the_feature(state: Path, tmp_path: Path):
    store = mapped(tmp_path)
    store.create_issue("other", "ACME", "Elsewhere", "", None, [])
    empty = tmp_path / "empty.md"
    empty.write_text("  \n")
    store.writes.clear()
    with pytest.raises(Refusal, match="empty"):
        Sitting(store, lambda: START).apply(["ACME-1", *TEAM, "--file", str(empty)])
    with pytest.raises(Refusal, match="ACME-2 is not a story of Alpha"):
        Sitting(store, lambda: START).apply(["ACME-2", *TEAM, "--file", str(empty)])
    assert store.writes == []
