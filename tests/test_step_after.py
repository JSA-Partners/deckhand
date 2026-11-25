from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand import chores, implplan
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.after import After
from deckhand.steps.decide import Decide
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import TEAM, approve_everything, setup
from tests.test_implplan import manifest, repo
from tests.test_step_build import BRANCH, Shell, labeled
from tests.test_step_review import answer, state

__all__ = ["repo", "state"]

CHORES = ["drop the old queue table", "rotate the webhook secret"]


def chored() -> FakeStore:
    store = setup()
    approve_everything(store)
    plan = Record(implplan.PURPOSE, "s1", "p1", "agent:implplan", manifest(after_merge=CHORES))
    store.put_record("s1", plan)
    return store


def listing(store: FakeStore, capsys: pytest.CaptureFixture[str]) -> dict:
    capsys.readouterr()
    After(store).context(["ACME-1", *TEAM, "--json"])
    return json.loads(capsys.readouterr().out)


def statuses(store: FakeStore, capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [item["status"] for item in listing(store, capsys)["items"]]


def puts(store: FakeStore) -> list:
    return [w for w in store.writes if w[0] == "put_record"]


def test_after_items_open_once_done(capsys: pytest.CaptureFixture[str]):
    store = chored()
    before = listing(store, capsys)
    assert (before["done"], [i["text"] for i in before["items"]]) == (False, CHORES)
    assert {i["status"] for i in before["items"]} == {"waits for the merge"}
    written = list(store.writes)
    with pytest.raises(Refusal, match="ACME-1 is not Done"):
        After(store).apply(["ACME-1", "done", "1", "--note", "dropped", *TEAM])
    assert store.writes == written
    store.set_issue_state("s1", "Done")
    after = listing(store, capsys)
    assert after["done"] is True
    assert [(i["n"], i["status"]) for i in after["items"]] == [(1, "open"), (2, "open")]


def test_hand_ticks_an_item(capsys: pytest.CaptureFixture[str]):
    store = chored()
    store.set_issue_state("s1", "Done")
    After(store).apply(["ACME-1", "done", "1", "--note", "dropped in migration 12", *TEAM])
    [record] = [r for r in store.records("s1") if r.kind == chores.AFTER_DONE]
    assert (record.subject, record.data["note"]) == ("s1:1", "dropped in migration 12")
    assert statuses(store, capsys) == ["done by the hand", "open"]
    written = list(store.writes)
    with pytest.raises(Refusal, match="item 1 of ACME-1 is done by the hand"):
        After(store).apply(["ACME-1", "done", "1", "--note", "again", *TEAM])
    with pytest.raises(Refusal, match="no item 3"):
        After(store).apply(["ACME-1", "done", "3", "--note", "x", *TEAM])
    with pytest.raises(Refusal, match="--note"):
        After(store).apply(["ACME-1", "done", "2", *TEAM])
    store.assign("s1", "someone-else")
    with pytest.raises(Refusal, match="ACME-1 belongs to someone-else"):
        After(store).apply(["ACME-1", "done", "2", "--note", "rotated", *TEAM])
    assert puts(store) == [w for w in written if w[0] == "put_record"]
    store.assign("s1", store.viewer()["id"])
    store.set_issue_state("s2", "Done")
    After(store).apply(["ACME-1", "done", "2", "--note", "rotated", *TEAM])
    assert statuses(store, capsys) == ["done by the hand", "done by the hand"]


def test_item_left_for_person_is_asked(state: Path, capsys: pytest.CaptureFixture[str]):
    store = chored()
    store.set_issue_state("s1", "Done")
    store.set_issue_state("s2", "Done")
    After(store).apply(["ACME-1", "person", "2", "--why", "the secret lives in the vault", *TEAM])
    records = store.records("s1")
    assert chores.after_merge_for_person(records, "s1") == [(2, CHORES[1], "the secret lives in the vault")]
    assert statuses(store, capsys) == ["open", "left for the person"]
    question = answer(state, Decide(store), capsys, ["after-merge", "ACME-1", "--item", "2"], "Drop")
    assert [o["label"] for o in question["options"]] == ["Done", "Drop", "Make it a story"]
    assert CHORES[1] in question["question"]
    assert capsys.readouterr().out == "done: answered Drop, no after-merge recorded\n"
    assert chores.after_merge_for_person(store.records("s1"), "s1") != []
    answer(state, Decide(store), capsys, ["after-merge", "ACME-1", "--item", "2"], "Done")
    [decided] = [r for r in store.records("s1") if r.kind == chores.AFTER]
    assert (decided.subject, decided.data["answer"]) == ("s1:2", "Done")
    assert chores.after_merge_for_person(store.records("s1"), "s1") == []
    assert statuses(store, capsys) == ["open", "done by the person"]


def test_after_context_shows_dropped_and_made_into_a_story(state: Path, capsys: pytest.CaptureFixture[str]):
    store = chored()
    store.set_issue_state("s1", "Done")
    store.assign("s1", store.viewer()["id"])
    for item in ("1", "2"):
        After(store).apply(["ACME-1", "person", item, "--why", "it needs main", *TEAM])
    answer(state, Decide(store), capsys, ["after-merge", "ACME-1", "--item", "1"], "nobody reads that table")
    answer(state, Decide(store), capsys, ["after-merge", "ACME-1", "--item", "2"], "Make it a story")
    found = listing(store, capsys)["items"]
    assert [(i["status"], i.get("note")) for i in found] == [
        ("dropped", "nobody reads that table"),
        ("made into a story", None),
    ]
    capsys.readouterr()
    After(store).context(["ACME-1", *TEAM])
    shown = capsys.readouterr().out.splitlines()
    assert shown[1] == f"1 {CHORES[0]}: dropped (nobody reads that table)"
    assert shown[2].startswith(f"2 {CHORES[1]}: made into a story (Chore: {CHORES[1]}")


def test_after_merge_decision_refused_for_hand_item(capsys: pytest.CaptureFixture[str]):
    store = chored()
    store.set_issue_state("s1", "Done")
    After(store).apply(["ACME-1", "done", "1", "--note", "dropped", *TEAM])
    written = list(store.writes)
    for item in ("1", "2"):
        with pytest.raises(Refusal, match=f"item {item} of ACME-1 is not left for the person"):
            Decide(store).context(["after-merge", "ACME-1", "--item", item, *TEAM])
    assert store.writes == written


SYNCED = [
    ["git", "rev-parse", "--verify", "--quiet", "origin/HEAD"],
    ["git", "status", "--porcelain", "--ignore-submodules=all"],
    ["git", "fetch", "--no-recurse-submodules", "origin"],
    ["git", "merge", "--no-edit", "origin/HEAD"],
    ["git", "rev-parse", "HEAD"],
]


def test_after_merge_work_takes_fresh_main_into_the_worktree(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    store.set_issue_state("s1", "Done")
    shell = Shell()
    written = list(store.writes)
    capsys.readouterr()
    After(store, shell, repo).apply(["ACME-1", "sync", *TEAM])
    assert shell.argvs == SYNCED
    assert store.writes == written
    assert capsys.readouterr().out == "done: merged origin/HEAD into ACME-1 at c2\n"


def test_after_merge_sync_refuses_before_the_merge_and_on_a_dirty_worktree(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    shell = Shell(dirty=True)
    with pytest.raises(Refusal, match="ACME-1 is not Done"):
        After(store, shell, repo).apply(["ACME-1", "sync", *TEAM])
    store.set_issue_state("s1", "Done")
    with pytest.raises(Refusal, match="uncommitted changes") as refused:
        After(store, shell, repo).apply(["ACME-1", "sync", *TEAM])
    assert refused.value.rule.endswith(LEAVE)
    (repo / ".worktrees" / BRANCH).rmdir()
    with pytest.raises(Refusal, match="ACME-1 has no worktree"):
        After(store, shell, repo).apply(["ACME-1", "sync", *TEAM])
    assert shell.ran("git", "fetch") == [] and shell.ran("git", "merge") == []


LEAVE = "leave the item that needs main to the person with after apply ACME-1 person N --why TEXT"


class Conflicted(Shell):
    def __call__(self, argv, cwd=None, stdin=None):
        if argv[:2] == ["git", "merge"]:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0 if "--abort" in argv else 1, "", "CONFLICT")
        if argv[:2] == ["git", "diff"] and "--diff-filter=U" in argv:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, "a.txt\n", "")
        return super().__call__(argv, cwd, stdin)


def test_an_after_merge_sync_conflict_leaves_the_item_to_the_person(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    store.set_issue_state("s1", "Done")
    shell = Conflicted()
    with pytest.raises(Refusal, match=r"conflicts in a\.txt") as refused:
        After(store, shell, repo).apply(["ACME-1", "sync", *TEAM])
    assert refused.value.rule.endswith(LEAVE)
    assert shell.ran("git", "merge") == [["git", "merge", "--no-edit", "origin/HEAD"], ["git", "merge", "--abort"]]
    assert "build apply" not in refused.value.rule
