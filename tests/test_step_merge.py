from __future__ import annotations

from pathlib import Path

import pytest

from deckhand import signoff
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.steps.merge import Merge
from deckhand.store.fake import FakeStore
from tests.test_quiz import ask, decide, item, reply, submit
from tests.test_step_build import Shell, repo, state
from tests.test_step_review import TEAM, answer, checked, raw
from tests.test_step_signoff import (
    Clock,
    Gh,
    branch_round,
    overview,
    ready,
    save_summary,
    sign,
    signable,
    voids,
)

__all__ = ["repo", "state"]


def merge(store: FakeStore, repo: Path, gh: Gh, clock: Clock | None = None) -> None:
    clock = clock or Clock()
    Merge(store, gh, repo, clock.sleep, clock).apply(["s1", *TEAM])


def test_merge_pins_signed_head(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys)
    question = sign(store, repo, state, capsys)
    assert [o["label"] for o in question["options"]] == ["Sign off", "Not yet"]
    assert "head c2" in question["question"]
    gh = Gh(buckets=[["pending"], ["pass"]])
    clock = Clock()
    merge(store, repo, gh, clock)
    [merged] = gh.ran("gh", "pr", "merge")
    assert merged == ["gh", "pr", "merge", "7", "--repo", "acme/widgets", "--squash", "--match-head-commit", "c2"]
    assert gh.ran("gh", "pr", "ready") == [["gh", "pr", "ready", "7", "--repo", "acme/widgets"]]
    assert "--auto" not in merged and clock.slept == [15]
    assert voids(store) == []


def test_merge_refuses_when_head_moved(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    assert overview(store, repo, capsys)["missing"] == []
    gh = Gh(pr_head="c3")
    with pytest.raises(Refusal, match="void, head moved"):
        merge(store, repo, gh)
    assert gh.ran("gh", "pr", "merge") == [] and gh.ran("gh", "pr", "ready") == []
    [void] = voids(store)
    nonce = signoff.signed(store.records("s1"), "s1").data["nonce"]
    assert (void.subject, void.data["head"]) == (f"s1:{nonce}", "c2")
    assert overview(store, repo, capsys)["missing"] == ["the sign-off at c2 is void: " + void.data["reason"]]
    with pytest.raises(Refusal, match="no current sign-off"):
        merge(store, repo, Gh())


def test_a_fresh_sign_off_after_a_void_is_current(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    with pytest.raises(Refusal, match="checks failed"):
        merge(store, repo, Gh(buckets=[["fail"]]))
    assert not signoff.current(store.records("s1"), "s1", "c2")
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")
    gh = Gh()
    merge(store, repo, gh)
    assert len(gh.ran("gh", "pr", "merge")) == 1


def test_unpushed_signed_head_refuses_without_a_void(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    before = list(store.writes)
    gh = Gh(pr_head="c1", behind=True)
    with pytest.raises(Refusal, match="push the signed head first"):
        merge(store, repo, gh)
    assert store.writes == before and gh.ran("gh", "pr", "ready") == [] and gh.ran("gh", "pr", "merge") == []
    assert ["git", "merge-base", "--is-ancestor", "c1", "c2"] in gh.argvs


def test_checks_failing_after_pending_void_the_sign_off(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    gh, clock = Gh(buckets=[["pending"], ["fail"]]), Clock()
    with pytest.raises(Refusal, match="checks failed: check 0"):
        merge(store, repo, gh, clock)
    assert clock.slept == [15] and gh.ran("gh", "pr", "merge") == []
    assert [v.data["reason"] for v in voids(store)] == ["checks failed: check 0"]


def test_merge_refuses_when_github_finds_the_head_moved(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    with pytest.raises(Refusal, match="void, head moved"):
        merge(store, repo, Gh(moved=True))
    assert [v.data["head"] for v in voids(store)] == ["c2"]


def test_merge_refuses_on_failed_checks(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    gh = Gh(buckets=[["pass", "fail"]])
    with pytest.raises(Refusal, match="checks failed: check 1"):
        merge(store, repo, gh)
    assert gh.ran("gh", "pr", "merge") == []
    assert [v.data["reason"] for v in voids(store)] == ["checks failed: check 1"]
    assert "void" in overview(store, repo, capsys)["missing"][0]


def test_pending_checks_stop_after_a_hundred_seconds_without_a_record(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    before = list(store.writes)
    gh, clock = Gh(buckets=[["pending"]]), Clock()
    capsys.readouterr()
    merge(store, repo, gh, clock)
    assert "checks pending, run merge apply ACME-1 again" in capsys.readouterr().out
    assert clock.now == 100 and gh.ran("gh", "pr", "merge") == [] and store.writes == before


def test_not_yet_after_sign_off_is_not_current(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    sign(store, repo, state, capsys, "Not yet")
    assert overview(store, repo, capsys)["missing"] == ["the person answered Not yet"]
    with pytest.raises(Refusal, match="no current sign-off"):
        merge(store, repo, Gh())


def test_proceed_does_not_cover_later_commits(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = signable(tmp_path, repo)
    save_summary(store, tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    quizzer = decide(store, repo)
    reply(state, quizzer, ask(quizzer, capsys), "The idempotency key")
    checked(store, "c2")
    for n in (1, 2, 3):
        branch_round(store, raw(severity="P1"), n=n, head="c1")
    cap = ["review-cap", "ACME-1", "--stage", "branch"]
    question = answer(state, Decide(store, runner=Shell(head="c1"), repo=repo), capsys, cap, "Proceed")
    assert "at c1" in question["question"]
    step = Decide(store, runner=Shell(), repo=repo)
    with pytest.raises(Refusal, match="the branch review has not reviewed the signed head c2"):
        step.context(["sign-off", "ACME-1", *TEAM])
    with pytest.raises(Refusal, match="no current sign-off"):
        merge(store, repo, Gh())
    question = answer(state, step, capsys, cap, "Proceed")
    assert "at c2" in question["question"]
    sign(store, repo, state, capsys)
    merge(store, repo, Gh())
