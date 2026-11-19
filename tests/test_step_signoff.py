from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand import review, signoff
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.steps.review import Review
from deckhand.steps.signoff import Merge, Signoff
from deckhand.store.fake import FakeStore
from tests.test_implplan import BASE, Git
from tests.test_quiz import ask, decide, item, reply, submit, summary
from tests.test_step_build import BRANCH, Shell, labeled, repo, state
from tests.test_step_review import PLAN, TEAM, answer, checked, raw, record_round
from tests.test_step_review import submit as submit_plan

__all__ = ["repo", "state"]


class Gh(Shell):
    def __init__(
        self, pr_head: str = "c2", buckets: list[list[str]] | None = None, moved: bool = False, behind: bool = False
    ) -> None:
        super().__init__()
        self.pr_head, self.moved, self.behind = pr_head, moved, behind
        self.buckets = buckets if buckets is not None else [["pass"]]

    def __call__(self, argv, cwd=None, stdin=None):
        if argv[:3] == ["git", "merge-base", "--is-ancestor"]:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0 if self.behind else 1, "", "")
        if argv[:3] == ["gh", "pr", "list"]:
            self.argvs.append(argv)
            found = [{"number": 7, "url": "https://example.test/pull/7", "isDraft": True, "headRefOid": self.pr_head}]
            return subprocess.CompletedProcess(argv, 0, json.dumps(found), "")
        if argv[:3] == ["gh", "pr", "checks"]:
            self.argvs.append(argv)
            now = self.buckets.pop(0) if len(self.buckets) > 1 else self.buckets[0]
            rows = [{"name": f"check {n}", "bucket": bucket} for n, bucket in enumerate(now)]
            return subprocess.CompletedProcess(argv, 0, json.dumps(rows), "")
        if argv[:3] == ["gh", "pr", "merge"] and self.moved:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 1, "", "Head branch was modified. Review and try the merge again.")
        return super().__call__(argv, cwd, stdin)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def branch_round(store: FakeStore, *items: dict, n: int = 1, head: str = "c2") -> None:
    found = review.parse_findings(json.dumps(list(items)))
    plan = review.RoundPlan(n, scoped=n > 1, base_hash="")
    store.put_record("s1", review.round_record("s1", "branch", plan, f"hash{n}", found, head))


def save_summary(store: FakeStore, tmp_path: Path, repo: Path, made: dict | None = None) -> None:
    path = tmp_path / "change.json"
    path.write_text(json.dumps(made if made is not None else summary(1)))
    Signoff(store, Shell(), repo).apply(["s1", "summary", "--summary", str(path), *TEAM])


def built(store: FakeStore, head: str = "c2") -> None:
    data = {"head": head, "base": "c1", "sha": None, "hunks": []}
    store.put_record("s1", Record(signoff.TUICR, "s1", head, "agent:signoff", data))


def signable(tmp_path: Path, repo: Path, tuicr: bool = True) -> FakeStore:
    store = labeled(tmp_path, repo)
    record_round(Review(store, Git(BASE), repo), tmp_path)
    if tuicr:
        built(store)
    return store


def ready(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str], tuicr: bool = True) -> FakeStore:
    store = signable(tmp_path, repo, tuicr)
    save_summary(store, tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    step = decide(store, repo)
    reply(state, step, ask(step, capsys), "The idempotency key")
    branch_round(store, raw())
    checked(store, "c2")
    return store


def sign(store: FakeStore, repo: Path, state: Path, capsys: pytest.CaptureFixture[str], choice: str = "Sign off"):
    return answer(state, Decide(store, runner=Shell(), repo=repo), capsys, ["sign-off", "ACME-1"], choice)


def merge(store: FakeStore, repo: Path, gh: Gh, clock: Clock | None = None) -> None:
    clock = clock or Clock()
    Merge(store, gh, repo, clock.sleep, clock).apply(["s1", *TEAM])


def voids(store: FakeStore) -> list:
    return [r for r in store.records("s1") if r.kind == signoff.VOID]


def overview(store: FakeStore, repo: Path, capsys: pytest.CaptureFixture[str]) -> dict:
    capsys.readouterr()
    Signoff(store, Shell(), repo).context(["s1", *TEAM, "--json"])
    return json.loads(capsys.readouterr().out)


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


def test_overview_without_a_worktree_lists_it_as_missing(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    (repo / ".worktrees" / BRANCH).rmdir()
    assert overview(store, repo, capsys)["missing"] == ["ACME-1 has no worktree on this machine"]


def test_commits_after_the_last_branch_round_need_a_review(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = signable(tmp_path, repo)
    save_summary(store, tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    step = decide(store, repo)
    reply(state, step, ask(step, capsys), "The idempotency key")
    branch_round(store, raw(), head="c1")
    checked(store, "c2")
    with pytest.raises(Refusal, match="the branch review has not reviewed the signed head c2"):
        Decide(store, runner=Shell(), repo=repo).context(["sign-off", "ACME-1", *TEAM])
    branch_round(store, raw(), n=2)
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")


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


def test_sign_off_refuses_with_open_quiz(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = signable(tmp_path, repo)
    save_summary(store, tmp_path, repo)
    submit(store, tmp_path, repo, [item()])
    branch_round(store, raw())
    checked(store, "c2")
    step = Decide(store, runner=Shell(), repo=repo)
    with pytest.raises(Refusal, match="quiz items still open: 1"):
        step.context(["sign-off", "ACME-1", *TEAM])
    quizzer = decide(store, repo)
    reply(state, quizzer, ask(quizzer, capsys), "A new key")
    with pytest.raises(Refusal, match="quiz items still open: 1"):
        step.context(["sign-off", "ACME-1", *TEAM])
    reply(state, quizzer, ask(quizzer, capsys), "The idempotency key")
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")


def test_sign_off_lists_everything_missing(tmp_path: Path, repo: Path):
    store = signable(tmp_path, repo)
    branch_round(store, raw(severity="P1"))
    with pytest.raises(Refusal) as refused:
        Decide(store, runner=Shell(), repo=repo).context(["sign-off", "ACME-1", *TEAM])
    rule = refused.value.rule
    for gap in ("no change summary", "no quiz is saved", "branch review has not stopped clean", "checks are not"):
        assert gap in rule


def test_summary_must_match_the_quiz(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys)
    other = summary(1) | {"intents": [{"title": "Queue orders", "lines": ["retries reuse the key"]}]}
    save_summary(store, tmp_path, repo, other)
    with pytest.raises(Refusal, match="differs from the one the quiz was written from"):
        Decide(store, runner=Shell(), repo=repo).context(["sign-off", "ACME-1", *TEAM])


def test_not_yet_after_sign_off_is_not_current(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    sign(store, repo, state, capsys, "Not yet")
    assert overview(store, repo, capsys)["missing"] == ["the person answered Not yet"]
    with pytest.raises(Refusal, match="no current sign-off"):
        merge(store, repo, Gh())


def test_a_new_local_commit_leaves_the_sign_off_behind(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    capsys.readouterr()
    Signoff(store, Shell(head="c3"), repo).context(["s1", *TEAM, "--json"])
    missing = json.loads(capsys.readouterr().out)["missing"]
    assert "the sign-off is for c2, not the head c3" in missing and "checks are not passing at c3" in missing


def test_branch_cap_proceed_clears_the_branch_review(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    for n in (1, 2, 3):
        branch_round(store, raw(severity="P1"), n=n)
    step = Decide(store, runner=Shell(), repo=repo)
    with pytest.raises(Refusal, match="branch review has not stopped clean"):
        step.context(["sign-off", "ACME-1", *TEAM])
    question = answer(state, step, capsys, ["review-cap", "ACME-1", "--stage", "branch"], "Proceed")
    assert question["question"].startswith("Three branch review rounds ran")
    assert [r.subject for r in store.records("s1") if r.kind == review.CAP_DECISION] == ["s1:branch"]
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")


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


def test_findings_screen_folds_doubtful_and_shows_declines(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sure, doubtful = raw(summary="Sure thing.", confidence=0.6), raw(summary="Maybe.", confidence=0.3)
    branch_round(store, raw(severity="P1", summary="Declined."), n=1)
    answer(
        state,
        Decide(store),
        capsys,
        ["finding", "ACME-1", "--finding", _id(raw(severity="P1", summary="Declined."))],
        "Decline",
    )
    branch_round(store, doubtful, sure, n=2, head="c1")
    capsys.readouterr()
    Signoff(store, Shell(), repo).context(["s1", *TEAM, "--screen", "3"])
    lines = capsys.readouterr().out.splitlines()
    assert lines.index("doubtful:") > next(i for i, line in enumerate(lines) if "Sure thing." in line)
    assert "Maybe." in lines[lines.index("doubtful:") + 1]
    assert "Declined." in lines[lines.index("declined by the person:") + 1]
    assert "reviewed at c1, not this head" in lines


def test_review_commit_is_built_once_per_head(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    shell = Diffing()
    for _ in range(2):
        Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    assert len(shell.ran("git", "commit")) == 1
    capsys.readouterr()
    Signoff(store, Shell(), repo).context(["s1", *TEAM, "--screen", "4"])
    out = capsys.readouterr().out
    assert "tuicr -r c1..c2" in out and "src/a.py: contract site" in out


class Diffing(Shell):
    def __call__(self, argv, cwd=None, stdin=None):
        if argv[:2] == ["git", "diff"]:
            self.argvs.append(argv)
            diff = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
            return subprocess.CompletedProcess(argv, 0, diff, "")
        if argv[:3] == ["git", "branch", "--show-current"]:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, "acme-1-orders\n", "")
        if argv[:2] == ["tuicr", "review"]:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        return super().__call__(argv, cwd, stdin)


def _id(found: dict) -> str:
    return review.finding_id(review.parse_findings(json.dumps([found]))[0], "branch")


def sign_off_refusal(store: FakeStore, repo: Path) -> str:
    with pytest.raises(Refusal) as refused:
        Decide(store, runner=Shell(), repo=repo).context(["sign-off", "ACME-1", *TEAM])
    return refused.value.rule


def test_sign_off_waits_for_the_person_on_a_delivery_finding(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    delivery = raw(changes_delivery=True)
    branch_round(store, delivery, n=2)
    assert f"{_id(delivery)} changes what the story delivers" in sign_off_refusal(store, repo)
    answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", _id(delivery)], "Decline")
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")


def test_sign_off_rechecks_the_plan_review_at_the_current_plan(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    submit_plan(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    assert "the plan review has not stopped clean at the current plan" in sign_off_refusal(store, repo)
    record_round(Review(store, Git(BASE), repo), tmp_path)
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")


def test_sign_off_needs_the_review_commit_at_the_head(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    assert "no tuicr review is built at c2" in sign_off_refusal(store, repo)
    Signoff(store, Diffing(), repo).apply(["s1", "review", *TEAM])
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")
