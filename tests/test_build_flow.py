from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand import features, review, signoff
from deckhand.features import Phase
from deckhand.step import Refusal
from deckhand.steps.build import Build
from deckhand.steps.checks import Checks
from deckhand.steps.decide import Decide
from deckhand.steps.merge import Merge
from deckhand.steps.review import Review
from deckhand.steps.signoff import Signoff
from tests.test_feature_approval import approve_everything, load, setup
from tests.test_step_build import BRANCH, repo, state
from tests.test_step_checks import Commands
from tests.test_step_review import PLAN, TEAM, answer, finding_id, raw, record_round, shown, submit
from tests.test_step_signoff import Clock, Diffing, Gh, overview, summary, voids

SHOWN = "src/a.py:1"

__all__ = ["repo", "state"]


class World(Gh, Diffing):
    def __call__(self, argv, cwd=None, stdin=None):
        if argv[:3] == ["gh", "pr", "create"]:
            self.created = True
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["gh", "pr", "list"] and not self.created:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        return super().__call__(argv, cwd, stdin)


class Hand:
    def __init__(self, tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.tmp, self.repo, self.state, self.capsys = tmp_path, repo, state, capsys
        self.world = World()
        self.world.head = "c1"
        self.store = setup()
        self.store.set_labels("s1", ["widgets"])
        self.store.assign("s1", self.store.viewer()["id"])
        approve_everything(self.store)

    def review(self) -> Review:
        return Review(self.store, self.world, self.repo)

    def decide(self) -> Decide:
        return Decide(self.store, runner=self.world, repo=self.repo)

    def plan_and_push(self) -> None:
        Build(self.store, self.world, self.repo).apply(["s1", "worktree", *TEAM])
        (self.repo / ".worktrees" / BRANCH).mkdir(parents=True)
        submit(self.store, self.tmp, self.repo)
        step = self.review()
        p1 = raw(severity="P1")
        record_round(step, self.tmp, p1)
        with pytest.raises(Refusal, match="has not stopped clean"):
            Build(self.store, self.world, self.repo).apply(["s1", "push", *TEAM])
        step.apply(["s1", "resolve", "--stage", "plan", *TEAM, "--accept", finding_id(p1)])
        submit(self.store, self.tmp, self.repo, PLAN + "- [ ] reuse the idempotency key\n")
        facts = shown(step, self.capsys, "s1", "--stage", "plan")
        assert (facts["round"], facts["scoped"], facts["stop"]) == (2, True, None)
        record_round(step, self.tmp)
        assert shown(step, self.capsys, "s1", "--stage", "plan")["stop"] == "clean"
        with pytest.raises(Refusal, match="no commits"):
            Build(self.store, self.world, self.repo).apply(["s1", "push", *TEAM])
        self.world.head = "c2"
        for _ in range(2):
            Build(self.store, self.world, self.repo).apply(["s1", "push", *TEAM])
        [create] = self.world.ran("gh", "pr", "create")
        assert "--draft" in create and create[create.index("--head") + 1] == BRANCH

    def check_and_review_branch(self, *items: dict) -> None:
        commands = Commands()
        Checks(self.store, self.world, self.repo, commands).apply(["s1", *TEAM])
        assert commands.ran == []
        step = self.review()
        facts = shown(step, self.capsys, "s1", "--stage", "branch")
        assert (facts["round"], facts["scoped"], facts["stop"]) == (1, False, None)
        record_round(step, self.tmp, *items, stage="branch")
        assert shown(step, self.capsys, "s1", "--stage", "branch")["stop"] == "clean"

    def summarize(self) -> None:
        made = self.tmp / "change.json"
        made.write_text(json.dumps(summary(1, where=SHOWN)))
        Signoff(self.store, self.world, self.repo).apply(["s1", "summary", "--summary", str(made), *TEAM])

    def sign_off(self) -> None:
        Signoff(self.store, self.world, self.repo).apply(["s1", "review", *TEAM])
        assert len(self.world.ran("git", "commit")) == 1
        question = answer(self.state, self.decide(), self.capsys, ["sign-off", "ACME-1"], "Sign off")
        assert [o["label"] for o in question["options"]] == ["Sign off", "Not yet"]
        assert signoff.current(self.store.records("s1"), "s1", self.world.head)

    def signed_at_head(self) -> None:
        self.summarize()
        self.sign_off()

    def through_sign_off(self) -> None:
        assert features.phase(load(self.store)) is Phase.BUILDING
        self.plan_and_push()
        self.check_and_review_branch()
        self.signed_at_head()

    def merge(self, clock: Clock | None = None) -> None:
        clock = clock or Clock()
        Merge(self.store, self.world, self.repo, clock.sleep, clock).apply(["s1", *TEAM])


def test_a_story_is_built_signed_off_and_merged_pinned(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    hand = Hand(tmp_path, repo, state, capsys)
    hand.through_sign_off()
    assert overview(hand.store, repo, capsys)["missing"] == []
    hand.world.buckets = [["pending"], ["pass"]]
    hand.merge()
    [merged] = hand.world.ran("gh", "pr", "merge")
    assert merged == ["gh", "pr", "merge", "7", "--repo", "acme/widgets", "--squash", "--match-head-commit", "c2"]
    assert "--auto" not in merged
    assert hand.world.ran("gh", "pr", "ready") == [["gh", "pr", "ready", "7", "--repo", "acme/widgets"]]
    assert voids(hand.store) == []


def test_a_head_that_moves_after_sign_off_voids_it(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    hand = Hand(tmp_path, repo, state, capsys)
    hand.through_sign_off()
    hand.world.pr_head = "c3"
    with pytest.raises(Refusal, match="void, head moved"):
        hand.merge()
    assert hand.world.ran("gh", "pr", "merge") == []
    assert hand.world.ran("gh", "pr", "ready") == [["gh", "pr", "ready", "7", "--repo", "acme/widgets", "--undo"]]
    [void] = voids(hand.store)
    assert void.data["head"] == "c2"
    assert not signoff.current(hand.store.records("s1"), "s1", "c2")
    [missing] = overview(hand.store, repo, capsys)["missing"]
    assert missing == "the sign-off at c2 is void: " + void.data["reason"]
    with pytest.raises(Refusal, match="no current sign-off"):
        hand.merge()
    assert hand.world.ran("gh", "pr", "merge") == []


def test_a_local_commit_after_sign_off_voids_it(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    hand = Hand(tmp_path, repo, state, capsys)
    hand.through_sign_off()
    hand.world.head = "c3"
    with pytest.raises(Refusal, match="void, head moved: the worktree is at c3"):
        hand.merge()
    assert hand.world.ran("gh", "pr", "merge") == []
    assert hand.world.ran("gh", "pr", "ready") == [["gh", "pr", "ready", "7", "--repo", "acme/widgets", "--undo"]]
    [void] = voids(hand.store)
    assert void.data["head"] == "c2"
    assert not signoff.current(hand.store.records("s1"), "s1", "c2")


def test_an_identical_diff_at_a_new_head_carries_the_review_forward(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    hand = Hand(tmp_path, repo, state, capsys)
    hand.plan_and_push()
    hand.check_and_review_branch(raw(severity="P2"))
    hand.world.head = "c3"
    Checks(hand.store, hand.world, hand.repo, Commands()).apply(["s1", *TEAM])
    step = hand.review()
    facts = shown(step, capsys, "s1", "--stage", "branch")
    assert (facts["stop"], facts["round"], facts["lenses"]) == ("carry", 2, [])
    capsys.readouterr()
    step.context(["s1", "--stage", "branch", *TEAM])
    text = capsys.readouterr().out
    assert f"target_hash: {facts['target_hash']}" in text and "no --findings" in text
    with pytest.raises(Refusal, match="carries the last round forward"):
        record_round(step, tmp_path, raw(severity="P1"), stage="branch")
    step.apply(["s1", "round", "--stage", "branch", *TEAM, "--target", facts["target_hash"]])
    first, carried = branch_rounds(hand.store)
    assert (carried.subject, carried.data["head"], carried.data["carry"]) == ("s1:branch:2", "c3", True)
    assert carried.data["findings"] == first.data["findings"] and len(first.data["findings"]) == 1
    assert shown(step, capsys, "s1", "--stage", "branch")["stop"] == "clean"
    left = finding_id(raw(severity="P2"), "branch")
    answer(hand.state, hand.decide(), capsys, ["finding", "ACME-1", "--finding", left], "Decline")
    hand.signed_at_head()
    hand.world.pr_head = "c3"
    hand.merge()
    [merged] = hand.world.ran("gh", "pr", "merge")
    assert merged[-2:] == ["--match-head-commit", "c3"]


def branch_rounds(store) -> list:
    found = [r for r in store.records("s1") if r.kind == review.KIND and r.subject.startswith("s1:branch:")]
    return sorted(found, key=lambda r: r.subject)
