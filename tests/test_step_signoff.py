from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from deckhand import asks, implplan, review, signoff, versions, wakes
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.checks import Checks
from deckhand.steps.decide import Decide
from deckhand.steps.merge import Merge
from deckhand.steps.review import Review
from deckhand.steps.signoff import SCREENS, Signoff
from deckhand.store.fake import FakeStore
from deckhand.summary import SHAPE, digest
from tests.test_implplan import BASE, Git
from tests.test_step_build import BRANCH, Shell, labeled, repo, state
from tests.test_step_review import PLAN, TEAM, answer, checked, raw, record_round
from tests.test_step_review import submit as submit_plan

__all__ = ["repo", "state"]

SOURCE = "".join(f"line {n}\n" for n in range(1, 31))
QUEUE_HUNK = "diff --git a/src/queue.py b/src/queue.py\n--- a/src/queue.py\n+++ b/src/queue.py\n@@ -10,3 +10,4 @@\n"
QUEUE_HUNK += " line 10\n line 11\n+line 12\n line 13\n"


def summary(new_here: int = 1, where: str = "src/queue.py:12") -> dict:
    entries = [{"text": f"new thing {n}", "path": "src/queue.py"} for n in range(new_here)]
    to_check = [{"where": where, "why": "a retry reuses the key"}]
    return {"intents": [], "new_here": entries, "screenshots": [], "state_diagram": None, "to_check": to_check}


class Gh(Shell):
    def __init__(
        self,
        pr_head: str = "c2",
        buckets: list[list[str]] | None = None,
        moved: bool = False,
        behind: bool = False,
        draft: bool = True,
    ) -> None:
        super().__init__()
        self.pr_head, self.moved, self.behind, self.draft = pr_head, moved, behind, draft
        self.buckets = buckets if buckets is not None else [["pass"]]

    def __call__(self, argv, cwd=None, stdin=None):
        if argv[:3] == ["git", "merge-base", "--is-ancestor"]:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0 if self.behind else 1, "", "")
        if argv[:3] == ["gh", "pr", "list"]:
            self.argvs.append(argv)
            found = [
                {"number": 7, "url": "https://example.test/pull/7", "isDraft": self.draft, "headRefOid": self.pr_head}
            ]
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


def branch_round(
    store: FakeStore, *items: dict, n: int = 1, head: str = "c2", carried: list[str] | None = None
) -> None:
    found = review.parse_findings(json.dumps(list(items)))
    plan = review.RoundPlan(n, scoped=n > 1, base_hash="")
    store.put_record("s1", review.round_record("s1", "branch", plan, f"hash{n}", found, head, carried or []))


def save_summary(store: FakeStore, tmp_path: Path, repo: Path, made: dict | None = None) -> None:
    path = tmp_path / "change.json"
    path.write_text(json.dumps(made if made is not None else summary(1)))
    Signoff(store, Diffing(diff=QUEUE_HUNK), repo).apply(["s1", "summary", "--summary", str(path), *TEAM])


def built(store: FakeStore, head: str = "c2") -> None:
    held = signoff.held_summary(store.records("s1"), "s1", head)
    data = {"head": head, "base": "c1", "sha": None, "hunks": [], "summary": digest(held) if held else None}
    store.put_record("s1", Record(signoff.TUICR, "s1", head, "agent:signoff", data))


def signable(tmp_path: Path, repo: Path, tuicr: bool = True) -> FakeStore:
    store = labeled(tmp_path, repo)
    store.assign("s1", store.viewer()["id"])
    record_round(Review(store, Git(BASE), repo), tmp_path)
    if tuicr:
        built(store)
    return store


def ready(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str], tuicr: bool = True) -> FakeStore:
    store = signable(tmp_path, repo, tuicr=False)
    save_summary(store, tmp_path, repo)
    if tuicr:
        built(store)
    branch_round(store, raw())
    fixed(store, raw())
    checked(store, "c2")
    return store


def fixed(store: FakeStore, *items: dict, stage: str = "branch") -> None:
    for found in review.parse_findings(json.dumps(list(items))):
        store.put_record("s1", review.accept_record(review.finding_id(found, stage), "hash1"))


def sign(store: FakeStore, repo: Path, state: Path, capsys: pytest.CaptureFixture[str], choice: str = "Sign off"):
    return answer(state, Decide(store, runner=Shell(), repo=repo), capsys, ["sign-off", "ACME-1"], choice)


def gap_text(store: FakeStore, head: str = "c2") -> str:
    return "; ".join(g.hand for g in signoff.gaps(store.records("s1"), "s1", head, "ACME-1"))


def not_ready_options(store: FakeStore, repo: Path, capsys: pytest.CaptureFixture[str]) -> list[str]:
    capsys.readouterr()
    Decide(store, runner=Shell(), repo=repo).context(["sign-off", "ACME-1", *TEAM, "--json"])
    asked = json.loads(capsys.readouterr().out)
    return [o["label"] for o in asked["tool_input"]["questions"][0]["options"]]


def voids(store: FakeStore) -> list:
    return [r for r in store.records("s1") if r.kind == signoff.VOID]


def overview(store: FakeStore, repo: Path, capsys: pytest.CaptureFixture[str]) -> dict:
    capsys.readouterr()
    Signoff(store, Shell(), repo).context(["s1", *TEAM, "--json"])
    return json.loads(capsys.readouterr().out)


class Showing(Shell):
    def __call__(self, argv, cwd=None, stdin=None):
        if argv[:2] == ["git", "show"]:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, SOURCE, "")
        return super().__call__(argv, cwd, stdin)


def test_a_to_check_entry_outside_the_diff_is_refused(tmp_path: Path, repo: Path):
    store = signable(tmp_path, repo)
    written = list(store.writes)
    made = summary() | {"to_check": [{"where": "src/queue.py:40", "why": "the key"}]}
    with pytest.raises(Refusal, match="to_check entry 0 names src/queue.py:40, which the diff does not show"):
        save_summary(store, tmp_path, repo, made)
    assert store.writes == written


DELETION = "diff --git a/src/old.py b/src/old.py\ndeleted file mode 100644\n--- a/src/old.py\n+++ /dev/null\n"
DELETION += "@@ -1,2 +0,0 @@\n-a\n-b\n"


def summarize(store: FakeStore, tmp_path: Path, repo: Path, made: dict, diff: str) -> None:
    path = tmp_path / "change.json"
    path.write_text(json.dumps(made))
    Signoff(store, Diffing(diff=diff), repo).apply(["s1", "summary", "--summary", str(path), *TEAM])


def test_a_diff_with_no_new_line_takes_an_empty_to_check(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = signable(tmp_path, repo)
    summarize(store, tmp_path, repo, summary() | {"to_check": []}, DELETION)
    assert signoff.held_summary(store.records("s1"), "s1", "c2") is not None
    capsys.readouterr()
    Signoff(store, Shell(), repo).context(["s1", *TEAM, "--screen", "4"])
    assert capsys.readouterr().out.splitlines()[1:] == ["nothing in the diff shows a new line to check"]


def test_a_diff_with_new_lines_refuses_an_empty_to_check(tmp_path: Path, repo: Path):
    store = signable(tmp_path, repo)
    written = list(store.writes)
    with pytest.raises(Refusal, match="to_check must hold at least one entry"):
        summarize(store, tmp_path, repo, summary() | {"to_check": []}, QUEUE_HUNK)
    assert store.writes == written


def test_screen_four_shows_each_line_to_check_with_its_code(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    shell = Showing()
    capsys.readouterr()
    Signoff(store, shell, repo).context(["s1", *TEAM, "--screen", "4"])
    assert capsys.readouterr().out.splitlines() == [
        "4 what to check for ACME-1 at c2",
        "src/queue.py:12: a retry reuses the key",
        "  11 line 11",
        "> 12 line 12",
        "  13 line 13",
    ]
    assert shell.ran("git", "show") == [["git", "show", "c2:src/queue.py"]]


def test_overview_without_a_worktree_lists_it_as_missing(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    (repo / ".worktrees" / BRANCH).rmdir()
    assert overview(store, repo, capsys)["missing"] == ["ACME-1 has no worktree on this machine"]


def test_commits_after_the_last_branch_round_need_a_review(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = signable(tmp_path, repo, tuicr=False)
    save_summary(store, tmp_path, repo)
    built(store)
    branch_round(store, raw(), head="c1")
    checked(store, "c2")
    assert re.search("the branch review has not reviewed the signed head c2", gap_text(store))
    branch_round(store, raw(), n=2)
    fixed(store, raw())
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")


def test_not_ready_sign_off_offers_not_yet_and_back_and_records_not_yet(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = signable(tmp_path, repo)
    save_summary(store, tmp_path, repo)
    assert not_ready_options(store, repo, capsys) == [signoff.NOT_YET, signoff.BACK]
    sign(store, repo, state, capsys, signoff.NOT_YET)
    [held] = [r for r in store.records("s1") if r.kind == signoff.KIND]
    assert held.data["answer"] == signoff.NOT_YET
    assert not signoff.current(store.records("s1"), "s1", "c2")


def test_an_early_not_yet_wakes_the_hand(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = signable(tmp_path, repo)
    start = Record(asks.HAND_START, "s1", "v1", "agent:hand", {})
    store.put_record("s1", start)
    save_summary(store, tmp_path, repo)
    assert asks.wake_due(store.records("s1"), "s1") is None
    sign(store, repo, state, capsys, signoff.NOT_YET)
    woken = asks.wake_due(store.records("s1"), "s1")
    assert woken is not None and woken.kind == signoff.KIND


def test_a_sign_off_in_the_persons_own_words_is_not_yet_and_reaches_the_hand(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    store.put_record("s1", Record(asks.HAND_START, "s1", "v1", "agent:hand", {}))
    sign(store, repo, state, capsys, "Tax first, then ship it.")
    [held] = [r for r in store.records("s1") if r.kind == signoff.KIND]
    assert (held.data["answer"], held.data["note"]) == (signoff.NOT_YET, "Tax first, then ship it.")
    assert not signoff.current(store.records("s1"), "s1", "c2")
    woken = asks.wake_due(store.records("s1"), "s1")
    assert woken == held
    assert "Reason: Tax first, then ship it." in wakes.body(store.issue("s1"), woken, store.records("s1"))


@pytest.mark.parametrize("words", ["Sign off, Not yet", ""])
def test_labels_or_nothing_typed_as_a_sign_off_are_refused(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str], words: str
):
    store = ready(tmp_path, repo, state, capsys)
    store.writes.clear()
    with pytest.raises(Refusal, match="is not one of Sign off, Not yet"):
        sign(store, repo, state, capsys, words)
    assert store.writes == []


def test_sign_off_on_a_not_ready_question_is_refused_and_writes_nothing(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = signable(tmp_path, repo)
    save_summary(store, tmp_path, repo)
    store.writes.clear()
    with pytest.raises(Refusal, match="is not one of Not yet, Back"):
        sign(store, repo, state, capsys, signoff.SIGN)
    assert store.writes == []
    assert not [r for r in store.records("s1") if r.kind == signoff.KIND]


def test_back_on_the_sign_off_question_records_nothing(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = signable(tmp_path, repo)
    save_summary(store, tmp_path, repo)
    store.writes.clear()
    sign(store, repo, state, capsys, signoff.BACK)
    assert store.writes == []
    assert "no sign-off recorded" in capsys.readouterr().out


def test_a_ready_sign_off_keeps_its_options(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    capsys.readouterr()
    Decide(store, runner=Shell(), repo=repo).context(["sign-off", "ACME-1", *TEAM, "--json"])
    asked = json.loads(capsys.readouterr().out)
    assert [o["label"] for o in asked["tool_input"]["questions"][0]["options"]] == [signoff.SIGN, signoff.NOT_YET]


def test_sign_off_lists_everything_missing(tmp_path: Path, repo: Path):
    store = signable(tmp_path, repo)
    branch_round(store, raw(severity="P1"))
    with pytest.raises(Refusal) as refused:
        Decide(store, runner=Shell(), repo=repo).context(["sign-off", "ACME-1", *TEAM])
    rule = refused.value.rule
    for gap in ("no change summary", "branch review has not stopped clean", "checks are not"):
        assert gap in rule


def test_a_new_local_commit_leaves_the_sign_off_behind(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    capsys.readouterr()
    Signoff(store, Shell(head="c3"), repo).context(["s1", *TEAM, "--json"])
    missing = json.loads(capsys.readouterr().out)["missing"]
    assert "the head moved to c3" in missing and "checks are not passing at c3" in missing


def test_branch_cap_proceed_clears_the_branch_review(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    for n in (1, 2, 3):
        branch_round(store, raw(severity="P1"), n=n)
    step = Decide(store, runner=Shell(), repo=repo)
    assert re.search("branch review has not stopped clean", gap_text(store))
    question = answer(state, step, capsys, ["review-cap", "ACME-1", "--stage", "branch"], "Proceed")
    assert question["question"].startswith("Three review rounds left a P1 open. Proceed to sign-off")
    assert [r.subject for r in store.records("s1") if r.kind == review.CAP_DECISION] == ["s1:branch:3"]
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")


def test_standing_names_void_reason(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    assert signoff.standing(store.records("s1"), "s1", "c3") == "the head moved to c3"
    with pytest.raises(Refusal, match="checks failed"):
        Merge(store, Gh(buckets=[["fail"]]), repo, Clock().sleep, Clock()).apply(["s1", *TEAM])
    voided = "the sign-off at c2 is void: checks failed: check 0"
    assert signoff.standing(store.records("s1"), "s1", "c3") == voided


def test_cap_question_skips_carry_rounds(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys)
    for n in (1, 2, 3):
        branch_round(store, raw(severity="P1"), n=n)
    last = review.last(review.rounds_of(store.records("s1"), "s1:branch"))
    carry = review.RoundPlan(4, scoped=True, base_hash="hash3", carry=True)
    store.put_record("s1", review.carry_record("s1", "branch", carry, last, "c2"))
    question = answer(
        state, Decide(store, Shell(), repo), capsys, ["review-cap", "ACME-1", "--stage", "branch"], "Proceed"
    )
    assert "3 rounds left a P1 open, 1 P1 open at c2" in question["question"]


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
    Signoff(store, Shell(), repo).context(["s1", *TEAM, "--screen", "5"])
    out = capsys.readouterr().out
    assert "tuicr -r c1..c2" in out and "src/a.py: contract site" in out


def test_a_revised_summary_at_the_same_head_rebuilds_the_review_commit(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    shell = Diffing()
    Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    summarize(store, tmp_path, repo, summary(where="src/a.py:1"), ONE_HUNK)
    Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    assert len(shell.ran("git", "commit")) == 2
    [hunk] = versions.held(store.records("s1"), signoff.TUICR, "s1").data["hunks"]
    assert hunk["reason"] == "contract site; to check: a retry reuses the key"


SLUG = "acme/widgets@acme-1-orders/commits/c2..c2"


ONE_HUNK = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
TWO_HUNKS = ONE_HUNK + "@@ -10 +10 @@\n-old10\n+new10\n"


class Diffing(Shell):
    def __init__(
        self, sessions: list[dict] | None = None, comments: list[dict] | None = None, diff: str = ONE_HUNK
    ) -> None:
        super().__init__()
        self.sessions, self.comments, self.diff = sessions or [], comments or [], diff
        self.broken = ""

    def __call__(self, argv, cwd=None, stdin=None):
        if argv[:2] == ["git", "diff"]:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, self.diff, "")
        if argv[:3] == ["git", "branch", "--show-current"]:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, "acme-1-orders\n", "")
        if argv[:2] == ["tuicr", "review"] and self.broken:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 1 if self.broken == "fails" else 0, self.broken, "")
        if argv[:2] == ["tuicr", "review"]:
            self.argvs.append(argv)
            out = {"list": self.sessions, "comments": self.comments}[argv[2]]
            return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")
        return super().__call__(argv, cwd, stdin)


def _id(found: dict) -> str:
    return review.finding_id(review.parse_findings(json.dumps([found]))[0], "branch")


def gaps_at_head(store: FakeStore, repo: Path) -> str:
    return gap_text(store)


def test_sign_off_waits_for_the_person_on_a_delivery_finding(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    delivery = raw(changes_delivery=True)
    branch_round(store, delivery, n=2)
    [gap] = [g for g in signoff.gaps(store.records("s1"), "s1", "c2", "ACME-1") if g.id == _id(delivery)]
    assert gap.hand.startswith(f"{_id(delivery)} changes what the story delivers; propose an answer")
    assert "decide" not in gap.hand
    store.put_record("s1", asks.proposal_record(_id(delivery), "v", "out of scope"))
    assert f"{_id(delivery)} waits for the person's decision" in gaps_at_head(store, repo)
    answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", _id(delivery)], "Decline")
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")


def test_sign_off_rechecks_the_plan_review_at_the_current_plan(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    submit_plan(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    assert "the plan review has not stopped clean at the current plan" in gaps_at_head(store, repo)
    record_round(Review(store, Git(BASE), repo), tmp_path)
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")


def test_sign_off_needs_the_review_commit_at_the_head(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    assert "no tuicr review is built at c2" in gaps_at_head(store, repo)
    Signoff(store, Diffing(), repo).apply(["s1", "review", *TEAM])
    sign(store, repo, state, capsys)
    assert signoff.current(store.records("s1"), "s1", "c2")


def tuicr_held(store: FakeStore) -> Record:
    [held] = [r for r in store.records("s1") if r.kind == signoff.TUICR]
    return held


def said(id: str, content: str) -> dict:
    return {"id": id, "location": "src/a.py:1", "path": "src/a.py", "start_line": 1, "side": "new", "content": content}


def test_the_persons_tuicr_comments_are_shown_apart_from_the_seeded_reasons(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    shell = Diffing(sessions=[{"slug": SLUG}], comments=[said("seed1", "contract site"), said("p1", "Rename this.")])
    Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    capsys.readouterr()
    Signoff(store, shell, repo).context(["s1", *TEAM, "--screen", "5", "--json"])
    shown = json.loads(capsys.readouterr().out)
    assert shown["comments"] == [said("p1", "Rename this.")]
    assert shown["lines"][-2:] == ["your comments:", "- src/a.py:1: Rename this."]
    assert overview(store, repo, capsys)["screens"]["tuicr"] == "built"
    Signoff(store, shell, repo).context(["s1", *TEAM, "--json"])
    shown = json.loads(capsys.readouterr().out)
    assert shown["screens"]["tuicr"] == "built, 1 comment from you"
    assert shown["comments"] == [said("p1", "Rename this.")]


@pytest.mark.parametrize("broken", ["fails", "not json"])
def test_the_tuicr_screen_prints_when_tuicr_cannot_be_read(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str], broken: str
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    shell = Diffing()
    Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    shell.broken = broken
    capsys.readouterr()
    Signoff(store, shell, repo).context(["s1", *TEAM, "--screen", "5", "--json"])
    shown = json.loads(capsys.readouterr().out)
    assert shown["comments"] == [] and "- src/a.py: contract site" in shown["lines"]


def test_the_tuicr_command_is_printed_from_the_story_worktree(
    tmp_path: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    spaced = tmp_path / "my repo"
    (spaced / ".claude").mkdir(parents=True)
    (spaced / ".claude" / "deckhand.toml").write_text('repository_label = "widgets"\n')
    store = ready(tmp_path, spaced, state, capsys, tuicr=False)
    worktree = shlex.quote(str(spaced / ".worktrees" / BRANCH))
    capsys.readouterr()
    Signoff(store, Diffing(), spaced).apply(["s1", "review", *TEAM])
    assert capsys.readouterr().out.splitlines()[1] == f"cd {worktree} && tuicr -r c1..c2"
    Signoff(store, Diffing(), spaced).context(["s1", *TEAM, "--screen", "5"])
    assert capsys.readouterr().out.splitlines()[2] == f"cd {worktree} && tuicr -r c1..c2"


def screen(store: FakeStore, repo: Path, capsys: pytest.CaptureFixture[str], n: int) -> list[str]:
    capsys.readouterr()
    Signoff(store, Shell(), repo).context(["s1", *TEAM, "--screen", str(n)])
    return capsys.readouterr().out.splitlines()


def plan_round(store: FakeStore, target: str, *items: dict) -> list[review.Finding]:
    found = review.parse_findings(json.dumps(list(items)))
    plan = review.RoundPlan(2, scoped=True, base_hash="")
    store.put_record("s1", review.round_record("s1", "plan", plan, target, found))
    return found


def test_findings_screen_lists_the_open_findings_of_the_last_plan_round(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    assert not any(line.startswith("plan round") for line in screen(store, repo, capsys, 3))
    kept, unsure = raw(summary="Kept open."), raw(summary="Unsure plan.", line=7, confidence=0.3)
    found = plan_round(store, "planhash", kept, raw(summary="Declined plan.", line=5), unsure)
    gone = review.finding_id(found[1], "plan")
    answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", gone], "Decline")
    lines = screen(store, repo, capsys, 3)
    start = lines.index("plan round 2, open:")
    assert start > lines.index("branch round 1") + 1
    section = lines[start + 1 : lines.index("declined by the person:")]
    assert section[0] == "reviewed plan version planhash, not the current one"
    assert "Kept open." in section[1] and section[2] == "doubtful:" and "Unsure plan." in section[3]
    assert len(section) == 4


def test_screen_3_prints_manifest_path(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys)
    saved = review.text_path(state, "s1", "planhash")
    plan_round(store, "planhash", raw(path=str(saved), summary="Kept open."))
    lines = screen(store, repo, capsys, 3)
    [shown] = [line for line in lines if "Kept open." in line]
    assert "PLAN.md:3" in shown and str(state) not in "\n".join(lines)


def test_findings_screen_shows_the_plan_round_before_any_branch_round(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = signable(tmp_path, repo)
    current = versions.held(store.records("s1"), implplan.PURPOSE, "s1")
    plan_round(store, current.version, raw(summary="Kept open."))
    lines = screen(store, repo, capsys, 3)
    assert lines[1:3] == ["the branch review has not run", "plan round 2, open:"]
    assert len(lines) == 4 and "Kept open." in lines[3]


def test_findings_screen_leaves_out_plan_findings_the_hand_fixed(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = signable(tmp_path, repo)
    current = versions.held(store.records("s1"), implplan.PURPOSE, "s1")
    [fixed] = plan_round(store, current.version, raw(summary="Fixed by the hand."))
    store.put_record("s1", review.accept_record(review.finding_id(fixed, "plan"), "v"))
    assert not any(line.startswith("plan round") for line in screen(store, repo, capsys, 3))


def test_review_apply_prints_each_selected_hunk_and_its_reason(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    capsys.readouterr()
    Signoff(store, Diffing(), repo).apply(["s1", "review", *TEAM])
    held = versions.held(store.records("s1"), signoff.TUICR, "s1")
    out = capsys.readouterr().out.splitlines()
    assert out[2:] == [f"- {h['path']}: {h['reason']}" for h in held.data["hunks"]] and len(out) > 2


def test_sign_off_of_a_story_in_another_repository_refuses(tmp_path: Path, repo: Path):
    store = signable(tmp_path, repo)
    store.set_labels("s1", ["gadgets"])
    with pytest.raises(Refusal, match="another repository"):
        Decide(store, runner=Shell(), repo=repo).context(["sign-off", "ACME-1", *TEAM])


@pytest.mark.parametrize(
    "step,args",
    [
        (Review, ["s1", "round", "--stage", "branch", "--target", "t", "--findings", "f.json"]),
        (Checks, ["s1"]),
        (Signoff, ["s1", "summary", "--summary", "s.json"]),
        (Merge, ["s1"]),
    ],
)
def test_every_hand_write_refuses_a_story_another_holds(tmp_path: Path, repo: Path, step: type, args: list[str]):
    store = signable(tmp_path, repo)
    store.assign("s1", "bob")
    store.writes.clear()
    with pytest.raises(Refusal, match="^ACME-1 belongs to bob$"):
        step(store, Shell(), repo, me=lambda: "ann").apply([*args, *TEAM])
    assert store.writes == []


def two_signers(repo: Path) -> None:
    (repo / ".claude" / "deckhand.toml").write_text('repository_label = "widgets"\nsecond_signoff = true\n')


def second(store: FakeStore, repo: Path, state: Path, capsys: pytest.CaptureFixture[str], by: str, choice: str):
    step = Decide(store, runner=Shell(), repo=repo, me=lambda: by)
    return answer(state, step, capsys, ["second-sign-off", "ACME-1"], choice)


def test_second_signoff_needs_another_person(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    two_signers(repo)
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    owner = store.viewer()["id"]
    assert signoff.current(store.records("s1"), "s1", "c2")
    assert not signoff.current(store.records("s1"), "s1", "c2", second=True)
    assert overview(store, repo, capsys)["missing"] == ["waiting for a second sign-off"]
    written = list(store.writes)
    with pytest.raises(Refusal, match=f"ACME-1 belongs to {owner}; a second sign-off comes from someone else"):
        second(store, repo, state, capsys, owner, "Sign off")
    assert store.writes == written
    question = second(store, repo, state, capsys, "teammate-2", "Sign off")
    assert [o["label"] for o in question["options"]] == ["Sign off", "Not yet"]
    [held] = [r for r in store.records("s1") if r.kind == signoff.SECOND]
    assert (held.version, held.data["by"]) == (
        signoff.second_version(signoff.signed(store.records("s1"), "s1")),
        "teammate-2",
    )
    assert signoff.current(store.records("s1"), "s1", "c2", second=True)
    assert overview(store, repo, capsys)["missing"] == []


def test_a_second_sign_off_in_the_persons_own_words_is_not_yet_with_its_reason(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    two_signers(repo)
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    second(store, repo, state, capsys, "teammate-2", "The refund path needs a test.")
    [held] = [r for r in store.records("s1") if r.kind == signoff.SECOND]
    assert (held.data["answer"], held.data["note"]) == (signoff.NOT_YET, "The refund path needs a test.")
    assert not signoff.current(store.records("s1"), "s1", "c2", second=True)


def test_second_signer_screens_from_records(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    (repo / ".worktrees" / BRANCH).rmdir()

    def shown(n: int) -> list[str]:
        capsys.readouterr()
        Signoff(store, Gh(), repo).context(["s1", "--screen", str(n), *TEAM])
        return capsys.readouterr().out.splitlines()

    for n in (1, 2, 3, 4):
        out = shown(n)
        assert out[0] == f"{n} {SCREENS[n - 1]} for ACME-1 at c2"
        assert not any("no worktree" in line or "not this head" in line for line in out)
    assert "checks: passed at this head" in shown(2)
    assert shown(5)[1:] == ["run gh pr diff 7 --repo acme/widgets in a terminal"]


def test_review_names_each_file_s_reason_once(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    shell = Diffing(sessions=[{"slug": SLUG}], diff=TWO_HUNKS)
    capsys.readouterr()
    Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    assert capsys.readouterr().out.splitlines()[2:] == ["- src/a.py: contract site"]
    Signoff(store, shell, repo).context(["s1", *TEAM, "--screen", "5"])
    assert capsys.readouterr().out.count("- src/a.py: contract site") == 1


def test_every_open_finding_the_hand_left_blocks_sign_off_until_it_is_decided(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    left, doubtful = raw(summary="Left open.", severity="P3"), raw(summary="Unsure.", line=9, confidence=0.2)
    fixed = raw()
    branch_round(store, fixed, left, doubtful)
    ids = [review.finding_id(f, "branch") for f in review.parse_findings(json.dumps([fixed, left, doubtful]))]
    store.put_record("s1", review.accept_record(ids[0], "hash1"))
    plan = versions.held(store.records("s1"), implplan.PURPOSE, "s1")
    plan_left = review.finding_id(plan_round(store, plan.version, raw(summary="Plan left."))[0], "plan")
    missing = overview(store, repo, capsys)["missing"]
    assert missing == [
        *(f"finding {id} waits for the hand" for id in (plan_left, ids[1], ids[2])),
        "the person has not signed off",
    ]
    assert not_ready_options(store, repo, capsys) == [signoff.NOT_YET, signoff.BACK]
    for id in (plan_left, ids[1]):
        answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", id], "Decline")
    answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", ids[2]], "Accept")
    assert overview(store, repo, capsys)["missing"] == ["the person has not signed off"]


class Logged(Shell):
    def __call__(self, argv, cwd=None, stdin=None):
        if argv[:3] == ["git", "log", "--oneline"]:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, "c3 third change\nc2 second change\n", "")
        return super().__call__(argv, cwd, stdin)


def test_evidence_names_each_command(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys)
    assert screen(store, repo, capsys, 2)[1:] == ["checks: passed at this head", "no checks are configured"]
    data = {"head": "c2", "passed": True, "command": None, "tail": None, "commands": ["make lint", "make types"]}
    store.put_record("s1", Record("checks", "s1", "c2", "agent:checks", data))
    assert screen(store, repo, capsys, 2)[1:] == ["checks: passed at this head", "ran: make lint", "ran: make types"]


def test_findings_name_each_unreviewed_commit(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    branch_round(store, raw(), n=2, head="c1")
    capsys.readouterr()
    shell = Logged(head="c3")
    Signoff(store, shell, repo).context(["s1", *TEAM, "--screen", "3"])
    lines = capsys.readouterr().out.splitlines()
    at = lines.index("reviewed at c1, not this head")
    assert lines[at + 1 : at + 3] == ["- c3 third change", "- c2 second change"]
    assert shell.ran("git", "log") == [["git", "log", "--oneline", "c1..c3"]]


OTHER_FILE = "diff --git a/src/b.py b/src/b.py\n--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-old\n+new\n"


def test_the_tuicr_line_names_hunks_head_and_what_was_left_out(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    capsys.readouterr()
    Signoff(store, Diffing(diff=ONE_HUNK + OTHER_FILE), repo).apply(["s1", "review", *TEAM])
    line = "tuicr shows a review commit of 1 of 2 hunks from head c2; left out: src/b.py"
    assert capsys.readouterr().out.splitlines()[0] == f"done: {line}"
    assert screen(store, repo, capsys, 5)[1] == line
    assert tuicr_held(store).data["total"] == 2 and tuicr_held(store).data["left_out"] == ["src/b.py"]


def test_the_tuicr_line_says_when_nothing_was_left_out(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    Signoff(store, Diffing(), repo).apply(["s1", "review", *TEAM])
    assert (
        screen(store, repo, capsys, 5)[1]
        == "tuicr shows a review commit of 1 of 1 hunks from head c2; nothing left out"
    )


def test_signoff_review_returns_without_polling(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    shell = Diffing(sessions=[{"slug": SLUG, "active": True}])
    Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    assert shell.ran("tuicr", "review") == []
    with pytest.raises(TypeError):
        Signoff(store, shell, repo, lambda seconds: None)
    with pytest.raises(Refusal, match="unknown argument --watch"):
        Signoff(store, shell, repo).apply(["s1", "review", "--watch", *TEAM])


def test_comments_read_on_demand_after_done(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    shell = Diffing(sessions=[{"slug": SLUG}], comments=[said("p1", "Rename this.")])
    Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    assert shell.ran("tuicr", "review", "comments") == []
    Signoff(store, shell, repo).context(["s1", *TEAM, "--screen", "5"])
    assert capsys.readouterr().out.splitlines()[-2:] == ["your comments:", "- src/a.py:1: Rename this."]


def test_a_review_commit_built_for_an_older_summary_counts_as_not_built(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    assert "no tuicr review is built" not in gap_text(store)
    summarize(store, tmp_path, repo, summary(2), QUEUE_HUNK)
    assert "no tuicr review is built at c2" in gap_text(store)


def test_a_finding_left_open_in_an_earlier_round_stays_the_persons(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    left = raw(summary="Left open.", severity="P3")
    branch_round(store, raw(), left)
    branch_round(store, n=2)
    plan = versions.held(store.records("s1"), implplan.PURPOSE, "s1")
    plan_left = review.finding_id(plan_round(store, "older", raw(summary="Plan left."))[0], "plan")
    store.put_record("s1", review.round_record("s1", "plan", review.RoundPlan(3, True, ""), plan.version, []))
    missing = "\n".join(overview(store, repo, capsys)["missing"])
    assert f"finding {_id(left)} waits for the hand" in missing
    assert f"finding {plan_left} waits for the hand" in missing
    assert not_ready_options(store, repo, capsys) == [signoff.NOT_YET, signoff.BACK]


OPEN = raw(path="cart.py", line=12, summary="The total skips tax.", fix="Add the tax line.")
UNFIXED = "accepted, not yet fixed: P2 correctness cart.py:12 The total skips tax. Fix: Add the tax line."


def as_hand(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "isolated-config"
    config.mkdir(exist_ok=True)
    (config / "config.toml").write_text(f'[clones]\nwidgets = "{repo}"\n', encoding="utf-8")
    monkeypatch.setenv("DECKHAND_ROLE", "hand")


def with_unfixed(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]) -> FakeStore:
    store = ready(tmp_path, repo, state, capsys)
    branch_round(store, raw(), OPEN, n=2)
    ids = [review.finding_id(found, "branch") for found in review.parse_findings(json.dumps([raw(), OPEN]))]
    for found in ids:
        store.put_record("s1", Record(review.DECISION, found, "hash2", "person:pat", {"answer": review.ACCEPT}))
    branch_round(store, OPEN, n=3, carried=ids)
    return store


def test_signoff_and_merge_contexts_list_unfixed_findings_for_a_hand(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    store = with_unfixed(tmp_path, repo, state, capsys)
    assert "unfixed" not in overview(store, repo, capsys)
    Signoff(store, Shell(), repo).context(["s1", *TEAM])
    assert UNFIXED not in capsys.readouterr().out.splitlines()
    as_hand(tmp_path, repo, monkeypatch)
    shown = overview(store, repo, capsys)
    assert [f["summary"] for f in shown["unfixed"]] == ["The total skips tax."]
    Signoff(store, Shell(), repo).context(["s1", *TEAM])
    assert capsys.readouterr().out.splitlines().count(UNFIXED) == 1
    Merge(store, Shell(), repo).context(["s1", *TEAM])
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["ACME-1: the person has not signed off", UNFIXED]


def test_signoff_gaps_name_the_story_ref(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    branch_round(store, raw(changes_delivery=True), raw(severity="P3", summary="Left."), n=2)
    found = signoff.gaps(store.records("s1"), "s1", "c2", "ACME-1")
    assert found
    assert not any("REF" in text for gap in found for text in (gap.hand, gap.person))
    assert any("ACME-1" in gap.hand for gap in found)


def test_the_overview_prints_hand_text_to_a_hand_and_person_text_to_the_person(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    store = ready(tmp_path, repo, state, capsys)
    branch_round(store, raw(severity="P3", summary="Left."), n=2)
    capsys.readouterr()
    Signoff(store, Shell(), repo).context(["s1", *TEAM])
    person = capsys.readouterr().out.splitlines()
    as_hand(tmp_path, repo, monkeypatch)
    Signoff(store, Shell(), repo).context(["s1", *TEAM])
    hand = capsys.readouterr().out.splitlines()
    assert person != hand
    assert any("waits for the hand" in line for line in person)
    assert any("review apply ACME-1 resolve --stage branch --accept" in line for line in hand)
    for lines in (person, hand):
        assert lines[0] == "missing:"
        assert lines.index("missing:") < next(i for i, line in enumerate(lines) if line.startswith("sign-off for"))


def test_a_hand_without_a_change_summary_is_shown_its_shape(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    store = signable(tmp_path, repo)
    as_hand(tmp_path, repo, monkeypatch)
    shape = f"- to_check: {SHAPE['to_check']}"
    assert shape in overview(store, repo, capsys)["lines"]
    save_summary(store, tmp_path, repo)
    assert shape not in overview(store, repo, capsys)["lines"]
