from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from deckhand import answers, quiz, tiers
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.steps.quiz import Quiz
from deckhand.store.fake import FakeStore
from deckhand.summary import SHAPE as SUMMARY_SHAPE
from deckhand.summary import digest, parse_summary
from tests.test_step_build import BRANCH, Shell, labeled, repo, state
from tests.test_step_review import TEAM

__all__ = ["repo", "state"]

HASH = re.compile(r"[0-9a-f]{12}")
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
        assert sorted(o["label"] for o in question["options"]) == sorted(item()["options"])
        assert "correct" not in pending and "recommended" not in json.dumps(question).lower()
        masked = HASH.sub("#", question["question"])
        kept = {k: v for k, v in pending.items() if k not in ("nonce", "version", "question", "options")}
        shown.append((masked, kept))
    assert shown[0] == shown[1]


def test_options_are_shown_in_an_order_seeded_by_the_set(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    step = decide(store, repo)
    orders = []
    listed = ["The order id", "The idempotency key", "A new key"]
    for agent in (item(), item(options=listed, correct=1)):
        submit(store, tmp_path, repo, [agent])
        for _ in range(2):
            orders.append([o["label"] for o in ask(step, capsys)["tool_input"]["questions"][0]["options"]])
    assert orders[0] == orders[1] == orders[2] == orders[3]
    [held] = [r for r in store.records("s1") if r.kind == quiz.SET]
    assert orders[0] == quiz.shown_options(held.version, 1, listed)


def test_key_change_voids_every_answer(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    step = decide(store, repo)
    reply(state, step, ask(step, capsys), "A new key")
    submit(store, tmp_path, repo, [item(correct=1)])
    assert not quiz.passed(store.records("s1"), "s1", "c2")
    assert quiz.open_items(store.records("s1"), "s1") == [1]
    reply(state, step, ask(step, capsys), "A new key")
    assert quiz.open_items(store.records("s1"), "s1") == []
    submit(store, tmp_path, repo, [item(correct=2)])
    assert quiz.open_items(store.records("s1"), "s1") == [1]


def test_set_records_the_canonical_summary_hash(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    [held] = [r for r in store.records("s1") if r.kind == quiz.SET]
    assert held.data["summary"] == digest(parse_summary(json.dumps(summary(), indent=4)))


def test_retry_with_a_malformed_worktree_record_notes_it(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    step = decide(store, repo)
    reply(state, step, ask(step, capsys), "A new key")
    store.put_record("s1", Record("worktree", "s1", BRANCH, "agent:build", {}))
    assert ask(step, capsys)["code"] == ["the worktree is not on this machine"]


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
    assert facts["summary_shape"] == SUMMARY_SHAPE


def test_only_a_person_answer_grades_an_item():
    held = quiz.set_record("s1", "c2", [quiz.Item(**item())], "digest", 1)
    by_agent = Record(quiz.KIND, quiz.subject("s1", 1), held.version, "agent:quiz", {"correct": True})
    assert quiz.open_items([held, by_agent], "s1") == [1]
    by_person = Record(quiz.KIND, quiz.subject("s1", 1), held.version, "person:pat", {"correct": True})
    assert quiz.open_items([held, by_person], "s1") == []


def test_a_located_question_ends_in_one_question_mark(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    step = decide(store, repo)
    reply(state, step, ask(step, capsys), "A new key")
    text = ask(step, capsys)["tool_input"]["questions"][0]["question"]
    assert "reuse (the code is at src/queue.py:12)? [" in text
    assert text.count("?") == 1


def test_a_wrong_answer_prints_the_code_that_answers_it(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    step = decide(store, repo)
    reply(state, step, ask(step, capsys), "A new key")
    printed = capsys.readouterr().out.splitlines()
    assert printed[0].endswith("wrong, the item stays open")
    assert printed[1] == "where: src/queue.py:12"
    assert "> 12 line 12" in printed


@pytest.mark.parametrize(
    "text,end", [("Goodbye\n", "the file ends with a newline"), ("Goodbye", "the file ends without a newline")]
)
def test_the_code_shows_how_the_file_ends(text: str, end: str):
    shown = quiz.code_at(Path("/w"), "goodbye.txt:1", lambda _: text)
    assert shown == ["> 1 Goodbye", f"  end of file: {end}"]
    assert quiz.code_at(Path("/w"), "src/queue.py:12", lambda _: SOURCE)[-1] == "  17 line 17"


DIFF = """diff --git a/src/queue.py b/src/queue.py
--- a/src/queue.py
+++ b/src/queue.py
@@ -10,3 +10,4 @@ def charge():
 line 10
 line 11
+line 12
 line 13
diff --git a/tests/test_queue.py b/tests/test_queue.py
--- a/tests/test_queue.py
+++ b/tests/test_queue.py
@@ -4,2 +4,3 @@
 line 4
+line 5
 line 6
"""


class Diffed(Shell):
    def __call__(self, argv, cwd=None, stdin=None):
        if argv[:2] == ["git", "diff"]:
            return subprocess.CompletedProcess(argv, 0, DIFF, "")
        return super().__call__(argv, cwd, stdin)


def submit_diffed(store: FakeStore, tmp_path: Path, repo: Path, where: str) -> None:
    found, made = tmp_path / "quiz.json", tmp_path / "summary.json"
    found.write_text(json.dumps([item(where=where)]))
    made.write_text(json.dumps(summary(1)))
    Quiz(store, Diffed(), repo).apply(["s1", *TEAM, "--quiz", str(found), "--summary", str(made)])


@pytest.mark.parametrize("where", ["tests/test_queue.py:5", "src/queue.py:40"])
def test_apply_refuses_an_item_outside_the_code_the_review_shows(tmp_path: Path, repo: Path, where: str):
    store = labeled(tmp_path, repo)
    written = list(store.writes)
    with pytest.raises(Refusal, match=rf"quiz item 1 asks about {re.escape(where)}.*src/queue.py lines 10-13"):
        submit_diffed(store, tmp_path, repo, where)
    assert store.writes == written


def test_apply_takes_an_item_inside_a_shown_hunk(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    submit_diffed(store, tmp_path, repo, "src/queue.py:12")
    assert quiz.held_set(store.records("s1"), "s1") is not None
