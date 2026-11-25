from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deckhand import features, planning, signoff
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.steps.merge import Merge
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import load
from tests.test_step_build import Shell, repo, state
from tests.test_step_review import TEAM, answer, checked, raw
from tests.test_step_signoff import (
    Clock,
    Gh,
    branch_round,
    built,
    gap_text,
    overview,
    ready,
    save_summary,
    second,
    sign,
    signable,
    two_signers,
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


def test_a_signed_story_merges_while_reapproval_is_due(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    planning.save_section(store, load(store), "Invariants", "Totals are positive.", False)
    assert load(store).reapproval_due and features.phase(load(store)) is features.Phase.BUILDING
    gh = Gh(buckets=[["pass"]])
    merge(store, repo, gh)
    assert len(gh.ran("gh", "pr", "merge")) == 1


def test_merge_refuses_when_head_moved(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    assert overview(store, repo, capsys)["missing"] == []
    gh = Gh(pr_head="c3")
    with pytest.raises(Refusal, match="void, head moved"):
        merge(store, repo, gh)
    assert gh.ran("gh", "pr", "merge") == [] and gh.ran("gh", "pr", "ready") == [
        ["gh", "pr", "ready", "7", "--repo", "acme/widgets", "--undo"]
    ]
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


def summarized(tmp_path: Path, repo: Path) -> FakeStore:
    store = signable(tmp_path, repo, tuicr=False)
    save_summary(store, tmp_path, repo)
    built(store)
    checked(store, "c2")
    return store


def test_proceed_does_not_cover_later_commits(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = summarized(tmp_path, repo)
    for n in (1, 2, 3):
        branch_round(store, raw(severity="P1"), n=n, head="c1")
    cap = ["review-cap", "ACME-1", "--stage", "branch"]
    question = answer(state, Decide(store, runner=Shell(head="c1"), repo=repo), capsys, cap, "Proceed")
    assert "at c1" in question["question"]
    assert "the branch review has not reviewed the signed head c2" in gap_text(store)
    with pytest.raises(Refusal, match="no current sign-off"):
        merge(store, repo, Gh())
    with pytest.raises(Refusal, match="has not reached its cap"):
        Decide(store, runner=Shell(), repo=repo).context([*cap, *TEAM])
    branch_round(store, n=4)
    sign(store, repo, state, capsys)
    merge(store, repo, Gh())


def test_a_sign_off_past_the_last_reviewed_head_never_merges(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = summarized(tmp_path, repo)
    for n in (1, 2, 3):
        branch_round(store, n=n, head="c1")
    gap = "the branch review has not reviewed the signed head c2; run a branch round"
    assert [g.hand for g in signoff.gaps(store.records("s1"), "s1", "c2", "ACME-1")] == [gap]
    held = signoff.version_at(store.records("s1"), "s1", "c2")
    data = {"answer": signoff.SIGN, "nonce": "n1", "by": "viewer-1"}
    store.put_record("s1", Record(signoff.KIND, "s1", held, "person:pat", data))
    gh = Gh()
    with pytest.raises(Refusal, match="has not reviewed the signed head"):
        merge(store, repo, gh)
    assert gh.ran("gh", "pr", "merge") == []


def test_void_returns_pr_to_draft(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    gh = Gh(buckets=[["fail"]])
    with pytest.raises(Refusal, match="void, checks failed"):
        merge(store, repo, gh)
    assert gh.ran("gh", "pr", "ready") == [
        ["gh", "pr", "ready", "7", "--repo", "acme/widgets"],
        ["gh", "pr", "ready", "7", "--repo", "acme/widgets", "--undo"],
    ]
    assert len(voids(store)) == 1


def test_merge_context_after_worktree_moved(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    capsys.readouterr()
    Merge(store, Shell(head="c3"), repo).context(["s1", *TEAM])
    out = capsys.readouterr().out
    assert "head moved: the worktree is at c3, signed at c2" in out and "ready" not in out


class Failing(Gh):
    def __init__(self, fails: tuple[str, ...], **kwargs) -> None:
        super().__init__(**kwargs)
        self.fails = fails

    def __call__(self, argv, cwd=None, stdin=None):
        if any(part in argv for part in self.fails):
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        return super().__call__(argv, cwd, stdin)


def test_a_void_whose_draft_call_failed_is_drafted_on_the_next_merge(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    with pytest.raises(Refusal, match="void, checks failed.*may still be ready"):
        merge(store, repo, Failing(("--undo",), buckets=[["fail"]], draft=False))
    assert len(voids(store)) == 1
    gh = Gh(draft=False)
    with pytest.raises(Refusal, match="no current sign-off"):
        merge(store, repo, gh)
    assert gh.ran("gh", "pr", "ready") == [["gh", "pr", "ready", "7", "--repo", "acme/widgets", "--undo"]]


def test_a_local_head_move_is_voided_when_github_cannot_be_reached(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    clock = Clock()
    unreachable = Failing(("view",))
    unreachable.head = "c3"
    with pytest.raises(Refusal, match="void, head moved: the worktree is at c3.*may still be ready"):
        Merge(store, unreachable, repo, clock.sleep, clock).apply(["s1", *TEAM])
    assert [v.data["head"] for v in voids(store)] == ["c2"]


def test_merge_waits_for_second_signoff(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    two_signers(repo)
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    gh = Gh()
    written = list(store.writes)
    with pytest.raises(Refusal, match="^ACME-1 is waiting for a second sign-off$"):
        merge(store, repo, gh)
    assert store.writes == written and gh.ran("gh", "pr", "merge") == [] and gh.ran("gh", "pr", "ready") == []
    second(store, repo, state, capsys, "teammate-2", "Sign off")
    merge(store, repo, gh)
    assert len(gh.ran("gh", "pr", "merge")) == 1


def test_second_signoff_does_not_outlive_a_void(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    two_signers(repo)
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    second(store, repo, state, capsys, "teammate-2", "Sign off")
    with pytest.raises(Refusal, match="checks failed"):
        merge(store, repo, Gh(buckets=[["fail"]]))
    sign(store, repo, state, capsys)
    gh = Gh()
    with pytest.raises(Refusal, match="^ACME-1 is waiting for a second sign-off$"):
        merge(store, repo, gh)
    assert gh.ran("gh", "pr", "merge") == []
    second(store, repo, state, capsys, "teammate-2", "Sign off")
    merge(store, repo, gh)
    assert len(gh.ran("gh", "pr", "merge")) == 1


def test_merge_context_of_a_merged_story_names_its_after_merge_work(capsys: pytest.CaptureFixture[str]):
    from tests.test_step_decide import feature_store, released

    store = released(feature_store())
    capsys.readouterr()
    Merge(store).context(["ACME-1", *TEAM])
    assert capsys.readouterr().out.splitlines() == [
        "ACME-1 has merged; do its after-merge items from deckhand after context ACME-1"
    ]


def test_merge_context_lists_unfixed_findings_once_signed_for_a_hand(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    from tests.test_step_signoff import UNFIXED, as_hand, with_unfixed

    store = with_unfixed(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    capsys.readouterr()
    Merge(store, Shell(), repo).context(["s1", *TEAM])
    assert UNFIXED not in capsys.readouterr().out.splitlines()
    as_hand(tmp_path, repo, monkeypatch)
    Merge(store, Shell(), repo).context(["s1", *TEAM])
    assert capsys.readouterr().out.splitlines()[1:] == [UNFIXED]
