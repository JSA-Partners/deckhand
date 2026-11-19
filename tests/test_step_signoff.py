from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from deckhand import implplan, review, signoff, versions
from deckhand.git import GitError
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.checks import Checks
from deckhand.steps.decide import Decide
from deckhand.steps.merge import Merge
from deckhand.steps.quiz import Quiz
from deckhand.steps.review import Review
from deckhand.steps.signoff import SCREENS, Signoff
from deckhand.store.fake import FakeStore
from tests.test_implplan import BASE, Git
from tests.test_quiz import ask, decide, item, reply, submit, summary
from tests.test_step_build import BRANCH, Shell, labeled, repo, state
from tests.test_step_review import PLAN, TEAM, answer, checked, raw, record_round
from tests.test_step_review import submit as submit_plan

__all__ = ["repo", "state"]


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
    store.assign("s1", store.viewer()["id"])
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


def voids(store: FakeStore) -> list:
    return [r for r in store.records("s1") if r.kind == signoff.VOID]


def overview(store: FakeStore, repo: Path, capsys: pytest.CaptureFixture[str]) -> dict:
    capsys.readouterr()
    Signoff(store, Shell(), repo).context(["s1", *TEAM, "--json"])
    return json.loads(capsys.readouterr().out)


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
    with pytest.raises(Refusal, match="branch review has not stopped clean"):
        step.context(["sign-off", "ACME-1", *TEAM])
    question = answer(state, step, capsys, ["review-cap", "ACME-1", "--stage", "branch"], "Proceed")
    assert question["question"].startswith("Three branch review rounds ran")
    assert [r.subject for r in store.records("s1") if r.kind == review.CAP_DECISION] == ["s1:branch"]
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
    assert "3 rounds, 1 P1 open" in question["question"]


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
        self.fail_add: int | None = None

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
        if argv[:3] == ["tuicr", "review", "add"]:
            self.argvs.append(argv)
            added = len(self.ran("tuicr", "review", "add"))
            if added == self.fail_add:
                return subprocess.CompletedProcess(argv, 1, "", "anchor rejected")
            path, line = argv[argv.index("--target-file") + 1], argv[argv.index("--line") + 1]
            made = {"id": f"seed{added}", "location": f"{path}:{line}", "path": path, "content": argv[-1]}
            self.comments.append(made)
            return subprocess.CompletedProcess(argv, 0, json.dumps(made), "")
        if argv[:2] == ["tuicr", "review"]:
            self.argvs.append(argv)
            out = {"list": self.sessions, "comments": self.comments}[argv[2]]
            return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")
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


def tuicr_held(store: FakeStore) -> Record:
    [held] = [r for r in store.records("s1") if r.kind == signoff.TUICR]
    return held


def test_reasons_are_seeded_once_per_session_after_the_person_comments_first(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    shell = Diffing(sessions=[{"slug": SLUG, "comment_count": 1}])
    for _ in range(2):
        Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    assert len(shell.ran("tuicr", "review", "add")) == 1
    assert "seeded" not in tuicr_held(store).data


def test_a_rerun_after_a_failed_add_seeds_only_the_missing_reason(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    shell = Diffing(sessions=[{"slug": SLUG}], diff=TWO_HUNKS)
    shell.fail_add = 2
    with pytest.raises(GitError):
        Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    shell.fail_add = None
    before = len(shell.ran("tuicr", "review", "add"))
    Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    [rerun] = shell.ran("tuicr", "review", "add")[before:]
    assert rerun[rerun.index("--line") + 1] == "10"


def test_a_session_holding_every_reason_is_left_alone_and_shows_none_as_the_persons(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    held = [said("t1", "contract site"), said("t2", "contract site"), said("p1", "Why?")]
    shell = Diffing(sessions=[{"slug": SLUG}], comments=held, diff=TWO_HUNKS)
    capsys.readouterr()
    Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    assert shell.ran("tuicr", "review", "add") == []
    assert capsys.readouterr().out.splitlines()[0].endswith("the reasons are seeded")
    Signoff(store, shell, repo).context(["s1", *TEAM, "--screen", "4", "--json"])
    assert json.loads(capsys.readouterr().out)["comments"] == [said("p1", "Why?")]


def said(id: str, content: str) -> dict:
    return {"id": id, "location": "src/a.py:1", "path": "src/a.py", "start_line": 1, "side": "new", "content": content}


def test_the_persons_tuicr_comments_are_shown_apart_from_the_seeded_reasons(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys, tuicr=False)
    shell = Diffing(sessions=[{"slug": SLUG}], comments=[said("seed1", "contract site"), said("p1", "Rename this.")])
    Signoff(store, shell, repo).apply(["s1", "review", *TEAM])
    capsys.readouterr()
    Signoff(store, shell, repo).context(["s1", *TEAM, "--screen", "4", "--json"])
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
    Signoff(store, shell, repo).context(["s1", *TEAM, "--screen", "4", "--json"])
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
    assert capsys.readouterr().out.startswith(f"done: cd {worktree} && tuicr -r c1..c2; ")
    Signoff(store, Diffing(), spaced).context(["s1", *TEAM, "--screen", "4"])
    assert capsys.readouterr().out.splitlines()[1] == f"cd {worktree} && tuicr -r c1..c2"


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
    assert out[1:] == [f"- {h['path']}: {h['reason']}" for h in held.data["hunks"]] and len(out) > 1


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
        (Quiz, ["s1", "--quiz", "q.json", "--summary", "s.json"]),
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

    for n in (1, 2, 3, 5):
        out = shown(n)
        assert out[0] == f"{n} {SCREENS[n - 1]} for ACME-1 at c2"
        assert not any("no worktree" in line or "not this head" in line for line in out)
    assert "checks: passed at this head" in shown(2)
    assert shown(4)[1:] == ["run gh pr diff 7 --repo acme/widgets in a terminal"]
