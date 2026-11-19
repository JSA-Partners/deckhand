from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import asks
from deckhand.step import Refusal
from deckhand.steps.ask import Ask
from deckhand.steps.decide import Decide
from deckhand.store.fake import FakeStore
from tests.test_step_decide import TEAM, feature_store, record_answer, state

__all__ = ["state"]

QUESTION = {"question": "Which queue?", "options": ["Redis", "SQS"], "why": "Both fit."}
EDIT = {"path": ".claude/settings.json", "change": "allow gh pr list", "why": "the hand lists pull requests"}
QID = asks.question_id("Which queue?")


def filed(tmp_path: Path, body: object, name: str = "in.json") -> str:
    path = tmp_path / name
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return str(path)


def puts(store: FakeStore) -> list:
    return [w for w in store.writes if w[0] == "put_record"]


def decided(state: Path, store: FakeStore, capsys: pytest.CaptureFixture[str], args: list[str], answer: str) -> dict:
    capsys.readouterr()
    Decide(store).context([*args, *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    record_answer(state, shown["tool_input"]["questions"][0], answer)
    Decide(store).apply([shown["nonce"], *TEAM])
    return shown["tool_input"]["questions"][0]


def listing(store: FakeStore, capsys: pytest.CaptureFixture[str]) -> dict:
    capsys.readouterr()
    Ask(store).context(["ACME-1", *TEAM, "--json"])
    return json.loads(capsys.readouterr().out)


def test_hand_question_recorded_once(tmp_path: Path):
    store = feature_store()
    path = filed(tmp_path, QUESTION)
    Ask(store).apply(["ACME-1", "--file", path, *TEAM])
    Ask(store).apply(["ACME-1", "--file", path, *TEAM])
    assert len(puts(store)) == 1
    [record] = store.records("s1")
    assert (record.kind, record.subject) == ("hand-question", f"s1:{QID}")
    assert record.data == QUESTION


@pytest.mark.parametrize(
    "over,match",
    [
        ({"options": ["Redis", "SQS (Recommended)"]}, "recommended"),
        ({"question": "Is SQS the recommended queue?"}, "recommended"),
    ],
)
def test_recommended_option_refused(tmp_path: Path, over: dict, match: str):
    store = feature_store()
    with pytest.raises(Refusal, match=match):
        Ask(store).apply(["ACME-1", "--file", filed(tmp_path, QUESTION | over), *TEAM])
    assert puts(store) == []


def test_five_options_refused(tmp_path: Path):
    store = feature_store()
    with pytest.raises(Refusal, match="2 to 4 options"):
        Ask(store).apply(["ACME-1", "--file", filed(tmp_path, QUESTION | {"options": list("abcde")}), *TEAM])
    assert puts(store) == []


def test_person_answer_reaches_ask_context(tmp_path: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = feature_store()
    Ask(store).apply(["ACME-1", "--file", filed(tmp_path, QUESTION), *TEAM])
    [held] = listing(store, capsys)["questions"]
    assert (held["id"], held["answer"]) == (QID, None)
    Ask(store).context(["ACME-1", *TEAM])
    assert f"{QID} Which queue? open" in capsys.readouterr().out
    asked = decided(state, store, capsys, ["question", QID, "--story", "ACME-1"], "SQS")
    assert [o["label"] for o in asked["options"]] == ["Redis", "SQS"]
    assert "Both fit." in asked["question"]
    [held] = listing(store, capsys)["questions"]
    assert held["answer"] == "SQS"
    [decision] = [r for r in store.records("s1") if r.kind == "question"]
    assert (decision.subject, decision.data["by"]) == (QID, "viewer-1")
    Ask(store).context(["ACME-1", *TEAM])
    assert f"{QID} Which queue? SQS" in capsys.readouterr().out


def test_free_text_answer_kept(tmp_path: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = feature_store()
    Ask(store).apply(["ACME-1", "--file", filed(tmp_path, QUESTION), *TEAM])
    decided(state, store, capsys, ["question", QID, "--story", "ACME-1"], "Neither, use a cron job")
    assert listing(store, capsys)["questions"][0]["answer"] == "Neither, use a cron job"


def test_config_edit_request_awaits_person(tmp_path: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = feature_store()
    Ask(store).apply(["ACME-1", "--edit", "--file", filed(tmp_path, EDIT), *TEAM])
    [request] = store.records("s1")
    item = request.subject.rpartition(":")[2]
    assert (request.kind, request.author, request.data["path"]) == ("config-edit", "agent:hand", EDIT["path"])
    assert len(item) == 12
    [held] = listing(store, capsys)["edits"]
    assert (held["id"], held["answer"]) == (item, None)
    asked = decided(state, store, capsys, ["config-edit", "ACME-1", "--item", item], "Done")
    assert [o["label"] for o in asked["options"]] == ["Done", "Not doing it"]
    assert ".claude/settings.json" in asked["question"]
    assert listing(store, capsys)["edits"][0]["answer"] == "Done"
    assert len(store.records("s1")) == 2


@pytest.mark.parametrize(
    "path",
    [
        ".claude/deckhand.toml",
        ".claude/deckhand/lenses/x.md",
        "./.claude/deckhand/",
        "sub/../.claude/deckhand.toml",
        "/work/repo/.claude/deckhand/lenses/x.md",
    ],
)
def test_config_edit_refused_for_deckhand_files(tmp_path: Path, path: str):
    store = feature_store()
    with pytest.raises(Refusal, match="edits those files itself"):
        Ask(store).apply(["ACME-1", "--edit", "--file", filed(tmp_path, EDIT | {"path": path}), *TEAM])
    assert puts(store) == []


def test_ask_refuses_a_story_that_belongs_to_another_person(tmp_path: Path):
    store = feature_store()
    store.assign("s1", "someone-else")
    with pytest.raises(Refusal, match="belongs to someone-else"):
        Ask(store).apply(["ACME-1", "--file", filed(tmp_path, QUESTION), *TEAM])
    assert puts(store) == []


def test_ask_refuses_an_unreadable_file(tmp_path: Path):
    with pytest.raises(Refusal, match="cannot be read"):
        Ask(feature_store()).apply(["ACME-1", "--file", str(tmp_path / "none.json"), *TEAM])
