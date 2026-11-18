from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import answers, quiz, tiers
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.steps.quiz import Quiz
from deckhand.store.fake import FakeStore
from tests.test_step_build import BRANCH, Shell, labeled, repo, state
from tests.test_step_review import TEAM

__all__ = ["repo", "state"]

SOURCE = "".join(f"line {n}\n" for n in range(1, 31))


def item(**over: object) -> dict:
    found = {
        "question": "What does a retried charge reuse?",
        "options": ["The idempotency key", "A new key", "The order id"],
        "correct": 0,
        "where": "src/queue.py:12",
    }
    return found | over


def summary(new_here: int = 1) -> dict:
    entries = [{"text": f"new thing {n}", "path": "src/queue.py"} for n in range(new_here)]
    return {"intents": [], "new_here": entries, "screenshots": [], "state_diagram": None}


def submit(store: FakeStore, tmp_path: Path, repo: Path, items: list[dict], new_here: int = 1) -> None:
    found, made = tmp_path / "quiz.json", tmp_path / "summary.json"
    found.write_text(json.dumps(items))
    made.write_text(json.dumps(summary(new_here)))
    Quiz(store, Shell(), repo).apply(["s1", *TEAM, "--quiz", str(found), "--summary", str(made)])


def reader(worktree: Path, files: dict[str, str]):
    def read(path: Path) -> str:
        name = path.relative_to(worktree).as_posix()
        if name not in files:
            raise FileNotFoundError(path)
        return files[name]

    return read


def decide(store: FakeStore, repo: Path, files: dict[str, str] | None = None) -> Decide:
    files = files if files is not None else {"src/queue.py": SOURCE}
    return Decide(store, runner=Shell(), repo=repo, reader=reader(repo / ".worktrees" / BRANCH, files))


def ask(step: Decide, capsys: pytest.CaptureFixture[str], n: int = 1) -> dict:
    capsys.readouterr()
    step.context(["quiz", "s1", "--item", str(n), *TEAM, "--json"])
    return json.loads(capsys.readouterr().out)


def reply(state: Path, step: Decide, asked: dict, choice: str) -> None:
    question = asked["tool_input"]["questions"][0]
    answers.record(
        {"session_id": "sess", "tool_response": {"questions": [question], "answers": {question["question"]: choice}}},
        state,
    )
    step.apply([asked["nonce"], *TEAM])


def test_quiz_size_by_risk():
    none, data_rule, multi_step, concurrency = (tiers.chosen(label) for label in reversed(tiers.OPTIONS))
    assert none.level == 0 and concurrency.level == 3
    assert quiz.quiz_size(none, 0) == 0
    assert quiz.quiz_size(none, 2) == 2
    assert quiz.quiz_size(data_rule, 0) == 1
    assert quiz.quiz_size(multi_step, 2) == 3
    assert quiz.quiz_size(concurrency, 1) == 4


def test_apply_refuses_a_quiz_of_the_wrong_size(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    before = list(store.writes)
    with pytest.raises(Refusal, match="has 2 items; its size by tier and new_here is 1"):
        submit(store, tmp_path, repo, [item(), item(question="Another?")])
    assert store.writes == before
    submit(store, tmp_path, repo, [], new_here=0)
    [held] = [r for r in store.records("s1") if r.kind == quiz.SET]
    assert (held.data["head"], held.data["items"], held.data["new_here"]) == ("c2", [], 0)
    assert quiz.passed(store.records("s1"), "s1", "c2")


@pytest.mark.parametrize(
    ("over", "rule"),
    [
        ({"options": ["Only one"]}, "2 to 4 options"),
        ({"options": ["a", "b", "c", "d", "e"]}, "2 to 4 options"),
        ({"correct": 3}, "correct must be an index into its 3 options"),
        ({"correct": True}, "correct must be an index into its 3 options"),
        ({"options": ["Same", "Same"]}, "duplicate option"),
        ({"options": ["The recommended key", "A new key"]}, "must not call itself recommended"),
        ({"where": "src/queue.py"}, "where must be path:line"),
        ({"where": "src/queue.py:0"}, "where must be path:line"),
        ({"where": "/etc/passwd:3"}, "relative path"),
        ({"where": "../secret.py:3"}, "relative path"),
        ({"hint": "the first"}, "unknown field hint"),
    ],
)
def test_apply_refuses_a_malformed_item(tmp_path: Path, repo: Path, over: dict, rule: str):
    store = labeled(tmp_path, repo)
    before = list(store.writes)
    with pytest.raises(Refusal, match=rule):
        submit(store, tmp_path, repo, [item(**over)])
    assert store.writes == before


def test_correct_index_not_in_question(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    step = decide(store, repo)
    shown = []
    for correct in (0, 2):
        submit(store, tmp_path, repo, [item(correct=correct)])
        asked = ask(step, capsys)
        pending = json.loads((state / "pending" / f"{asked['nonce']}.json").read_text())
        question = asked["tool_input"]["questions"][0]
        assert [o["label"] for o in question["options"]] == item()["options"]
        assert "correct" not in pending and "recommended" not in json.dumps(question).lower()
        shown.append(({k: v for k, v in question.items() if k != "header"}, pending | {"nonce": ""}))
    assert shown[0] == shown[1]


def test_wrong_answer_keeps_item_open_and_names_where(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    step = decide(store, repo)
    first = ask(step, capsys)
    assert "where" not in first and "src/queue.py:12" not in first["tool_input"]["questions"][0]["question"]
    reply(state, step, first, "A new key")
    assert "wrong" in capsys.readouterr().out
    [held] = [r for r in store.records("s1") if r.kind == quiz.KIND]
    assert (held.subject, held.data["correct"], held.data["answer"]) == ("s1:1", False, "A new key")
    assert quiz.open_items(store.records("s1"), "s1") == [1]
    assert not quiz.passed(store.records("s1"), "s1", "c2")

    again = ask(step, capsys)
    assert again["nonce"] != first["nonce"]
    assert again["where"] == "src/queue.py:12"
    assert again["code"][0].endswith("line 7") and again["code"][-1].endswith("line 17")
    assert [line for line in again["code"] if line.startswith(">")] == ["> 12 line 12"]
    assert "src/queue.py:12" in again["tool_input"]["questions"][0]["question"]
    step.context(["quiz", "s1", "--item", "1", *TEAM])
    printed = capsys.readouterr().out
    assert printed.index("where: src/queue.py:12") < printed.index("> 12 line 12") < printed.index("nonce:")

    reply(state, step, again, "The idempotency key")
    assert "right" in capsys.readouterr().out
    [held] = [r for r in store.records("s1") if r.kind == quiz.KIND]
    assert held.data["correct"] is True
    assert quiz.open_items(store.records("s1"), "s1") == []
    assert quiz.passed(store.records("s1"), "s1", "c2")
    assert not quiz.passed(store.records("s1"), "s1", "c3")
    with pytest.raises(Refusal, match="already answered right"):
        ask(step, capsys)


def test_retry_with_unreadable_code_still_asks(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    submit(store, tmp_path, repo, [item(where="src/gone.py:3")])
    step = decide(store, repo, {"src/queue.py": SOURCE})
    reply(state, step, ask(step, capsys), "A new key")
    again = ask(step, capsys)
    assert again["where"] == "src/gone.py:3"
    assert again["code"] == ["src/gone.py cannot be read in the worktree"]


def test_answer_to_a_replaced_quiz_refuses(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    step = decide(store, repo)
    asked = ask(step, capsys)
    submit(store, tmp_path, repo, [item(options=["Yes", "No"])])
    before = list(store.writes)
    with pytest.raises(Refusal, match="quiz changed"):
        reply(state, step, asked, "The idempotency key")
    assert store.writes == before


def test_item_flag_belongs_to_quiz(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    step = decide(store, repo)
    with pytest.raises(Refusal, match="no quiz is saved"):
        ask(step, capsys)
    submit(store, tmp_path, repo, [item()])
    with pytest.raises(Refusal, match="quiz needs --item N"):
        step.context(["quiz", "s1", *TEAM])
    with pytest.raises(Refusal, match="--item applies only to quiz"):
        step.context(["lean", "s1", "--item", "1", *TEAM])
    with pytest.raises(Refusal, match="no item 2"):
        ask(step, capsys, 2)


def test_context_lists_the_size_and_open_items(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    capsys.readouterr()
    Quiz(store, Shell(), repo).context(["s1", *TEAM, "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert (facts["head"], facts["base"], facts["open"], facts["quiz_head"]) == ("c2", 0, [1], "c2")
    assert facts["worktree"] == str(repo / ".worktrees" / BRANCH)
