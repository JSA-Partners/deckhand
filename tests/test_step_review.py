from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from deckhand import answers, asks, features, planning, review, review_gaps
from deckhand.features import FEATURE_LABEL
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.build import WORKTREE
from deckhand.steps.decide import Decide
from deckhand.steps.implplan import ImplPlan
from deckhand.steps.review import FeatureReview, Review
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import P1, approve_everything, decided, load, mapped, reapprove, setup
from tests.test_implplan import BASE, Git, manifest, repo

__all__ = ["repo"]

TEAM = ["--team-key", "ACME"]
PLAN = "# Plan\n\n- [ ] queue the order\n"


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def raw(**over: object) -> dict:
    item = {
        "lens": "correctness",
        "severity": "P2",
        "path": "plan.md",
        "line": 3,
        "summary": "A step contradicts a criterion.",
        "fix": "Reorder the steps.",
        "evidence": "plan.md:3 with an empty cart",
        "confidence": 0.9,
    }
    return item | over


def worktree(store: FakeStore, repo: Path, story: str = "s1") -> Path:
    store.put_record(story, Record(WORKTREE, story, "acme-1-queue", "agent:build", {"branch": "acme-1-queue"}))
    path = repo / ".worktrees" / "acme-1-queue"
    path.mkdir(parents=True, exist_ok=True)
    return path


def checked(store: FakeStore, head: str, story: str = "s1") -> None:
    store.put_record(story, Record("checks", story, head, "agent:checks", {"head": head, "passed": True}))


def finding_id(item: dict, stage: str = "plan") -> str:
    return review.finding_id(review.parse_findings(json.dumps([item]))[0], stage)


def submit(store: FakeStore, tmp_path: Path, repo: Path, text: str = PLAN) -> None:
    plan, found = tmp_path / "plan.md", tmp_path / "manifest.json"
    plan.write_text(text)
    found.write_text(json.dumps(manifest()))
    ImplPlan(store, Git(BASE), repo).apply(["s1", *TEAM, "--plan", str(plan), "--manifest", str(found)])


def planned(tmp_path: Path, repo: Path) -> FakeStore:
    store = setup()
    approve_everything(store)
    submit(store, tmp_path, repo)
    return store


def findings_file(tmp_path: Path, *items: dict) -> str:
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(list(items)))
    return str(path)


def where(step: Review, ref: str, stage: str) -> list[str]:
    return [] if isinstance(step, FeatureReview) else [ref, "--stage", stage]


def target_of(step: Review, ref: str = "s1", stage: str = "plan") -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        step.context([*where(step, ref, stage), *TEAM, "--json"])
    return json.loads(out.getvalue())["target_hash"]


def record_round(
    step: Review, tmp_path: Path, *items: dict, ref: str = "s1", stage: str = "plan", target: str | None = None
) -> None:
    found = where(step, ref, stage)
    pinned = target if target is not None else target_of(step, ref, stage)
    findings = findings_file(tmp_path, *items)
    step.apply([*found[:1], "round", *found[1:], *TEAM, "--findings", findings, "--target", pinned])


def shown(step: Review, capsys: pytest.CaptureFixture[str], *args: str) -> dict:
    capsys.readouterr()
    step.context([*args, *TEAM, "--json"])
    return json.loads(capsys.readouterr().out)


def rounds(store: FakeStore, story: str = "s1", owner: str = "") -> list:
    return [r for r in store.records(story) if r.kind == review.KIND and r.subject.startswith(f"{owner or story}:")]


def answer(state: Path, step: Decide, capsys: pytest.CaptureFixture[str], args: list[str], choice: str) -> dict:
    capsys.readouterr()
    step.context([*args, *TEAM, "--json"])
    asked = json.loads(capsys.readouterr().out)
    question = asked["tool_input"]["questions"][0]
    answers.record(
        {"session_id": "sess", "tool_response": {"questions": [question], "answers": {question["question"]: choice}}},
        state,
    )
    step.apply([asked["nonce"], *TEAM])
    return question


def test_agent_decline_refuses(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    p1, delivery = raw(severity="P1"), raw(summary="Orders ship a day later.", changes_delivery=True)
    record_round(step, tmp_path, p1, delivery)
    before = list(store.writes)
    resolve = ["s1", "resolve", "--stage", "plan", *TEAM]
    with pytest.raises(Refusal, match="only the person declines a finding.*--propose-decline"):
        step.apply([*resolve, "--decline", finding_id(p1)])
    found = finding_id(delivery)
    with pytest.raises(
        Refusal, match=f"changes what the story delivers.*--propose-accept {found}.*--propose-decline {found}"
    ):
        step.apply([*resolve, "--accept", finding_id(p1), "--accept", found])
    with pytest.raises(Refusal, match="not a finding of the last round"):
        step.apply([*resolve, "--accept", "000000000000"])
    with pytest.raises(Refusal, match="takes one resolution in a call"):
        step.apply([*resolve, "--accept", finding_id(p1), "--propose-decline", finding_id(p1), "--reason", "r"])
    assert store.writes == before
    step.apply([*resolve, "--accept", finding_id(p1)])
    [accept] = [r for r in store.records("s1") if r.kind == review.HAND_ACCEPT]
    assert (accept.subject, accept.author, accept.data["answer"]) == (finding_id(p1), "agent:review", "Accept")
    assert review.declined(store.records("s1")) == set()


def test_one_resolve_takes_accepts_and_proposals_together(tmp_path: Path, repo: Path, capsys):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    p1, p2 = raw(severity="P1"), raw(summary="Another gap.")
    delivery = raw(summary="Orders ship a day later.", changes_delivery=True)
    record_round(step, tmp_path, p1, p2, delivery)
    before = len(store.writes)
    resolve = ["s1", "resolve", "--stage", "plan", *TEAM, "--accept", finding_id(p1), "--accept", finding_id(p2)]
    step.apply([*resolve, "--propose-accept", finding_id(delivery), "--reason", "The spec allows a day."])
    written = store.writes[before:]
    assert len(written) == 3
    held = store.records("s1")
    assert {r.subject for r in held if r.kind == review.HAND_ACCEPT} == {finding_id(p1), finding_id(p2)}
    [proposal] = [r for r in held if r.kind == asks.PROPOSAL]
    assert (proposal.subject, proposal.data) == (
        finding_id(delivery),
        {"reason": "The spec allows a day.", "answer": review.ACCEPT},
    )
    assert "accepted 2 and proposed 1 findings" in capsys.readouterr().out


def test_a_second_reason_refuses_and_writes_nothing(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    one, two = raw(summary="One.", changes_delivery=True), raw(summary="Two.", changes_delivery=True)
    record_round(step, tmp_path, one, two)
    before = list(store.writes)
    resolve = ["s1", "resolve", "--stage", "plan", *TEAM, "--propose-decline", finding_id(one), "--reason", "out"]
    with pytest.raises(Refusal, match="one --reason covers every proposal in the call.*separate calls"):
        step.apply([*resolve, "--propose-accept", finding_id(two), "--reason", "in"])
    assert store.writes == before


def test_a_delivery_finding_of_an_earlier_round_can_still_be_proposed(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    delivery = raw(summary="Orders ship a day later.", changes_delivery=True)
    record_round(step, tmp_path, delivery)
    [first] = rounds(store)
    submit(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    record_round(step, tmp_path)
    resolve = ["s1", "resolve", "--stage", "plan", *TEAM]
    step.apply([*resolve, "--propose-decline", finding_id(delivery), "--reason", "out of scope"])
    [proposal] = [r for r in store.records("s1") if r.kind == asks.PROPOSAL]
    assert (proposal.subject, proposal.version) == (finding_id(delivery), first.version)
    [gap] = review_gaps.delivery_gaps(store.records("s1"), "s1", "plan", "ACME-1")
    assert gap.hand == f"{finding_id(delivery)} waits for the person's decision"


def test_a_re_proposal_after_a_change_of_mind_is_the_newest(tmp_path: Path, repo: Path, capsys):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    delivery = raw(summary="Orders ship a day later.", changes_delivery=True)
    record_round(step, tmp_path, delivery)
    resolve = ["s1", "resolve", "--stage", "plan", *TEAM]
    found = finding_id(delivery)
    step.apply([*resolve, "--propose-decline", found, "--reason", "out of scope"])
    step.apply([*resolve, "--propose-accept", found, "--reason", "in scope"])
    step.apply([*resolve, "--propose-decline", found, "--reason", "out of scope"])
    proposal = asks.proposed(store.records("s1"), found)
    assert proposal is not None and asks.proposed_answer(proposal) == review.DECLINE
    before = list(store.writes)
    step.apply([*resolve, "--propose-decline", found, "--reason", "out of scope"])
    assert store.writes == before


def test_decline_proposal_awaits_person(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    p1 = raw(severity="P1")
    record_round(step, tmp_path, p1)
    resolve = ["s1", "resolve", "--stage", "plan", *TEAM]
    before = list(store.writes)
    with pytest.raises(Refusal, match="--reason"):
        step.apply([*resolve, "--propose-decline", finding_id(p1)])
    with pytest.raises(Refusal, match="not a finding of the last round"):
        step.apply([*resolve, "--propose-decline", "000000000000", "--reason", "noise"])
    assert store.writes == before
    step.apply([*resolve, "--propose-decline", finding_id(p1), "--reason", "The plan already orders these."])
    [proposal] = [r for r in store.records("s1") if r.kind == "decline-proposal"]
    assert (proposal.subject, proposal.author) == (finding_id(p1), "agent:review")
    assert proposal.data["reason"] == "The plan already orders these."
    assert review.declined(store.records("s1")) == set()
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] != "clean"
    assert [r.subject for r in asks.open_proposals(store.records("s1"))] == [finding_id(p1)]
    answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", finding_id(p1)], "Decline")
    assert asks.open_proposals(store.records("s1")) == []
    with pytest.raises(Refusal, match="already decided"):
        step.apply([*resolve, "--propose-decline", finding_id(p1), "--reason", "again"])


def test_person_decline_through_decide(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    p1 = raw(severity="P1")
    record_round(step, tmp_path, p1)
    question = answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", finding_id(p1)], "Decline")
    assert [o["label"] for o in question["options"]] == ["Decline", "Accept"]
    assert "recommended" not in json.dumps(question).lower()
    assert "A step contradicts a criterion." in question["question"]
    [decision] = [r for r in store.records("s1") if r.kind == review.DECISION]
    assert (decision.subject, decision.author, decision.data["answer"]) == (finding_id(p1), "person:sess", "Decline")
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] == "clean"
    submit(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    facts = shown(step, capsys, "s1", "--stage", "plan")
    assert (facts["stop"], facts["round"], facts["scoped"]) == (None, 2, True)


def test_round_recorded_in_confidence_order(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    low, high, mid = raw(confidence=0.3, summary="low"), raw(confidence=0.95, summary="high"), raw(summary="mid")
    record_round(step, tmp_path, low, high, mid)
    [held] = rounds(store)
    assert [f["summary"] for f in held.data["findings"]] == ["high", "mid", "low"]
    assert held.subject == "s1:plan:1"
    step.context(["s1", "--stage", "plan", *TEAM])
    out = capsys.readouterr().out
    assert out.index("high") < out.index("mid") < out.index("doubtful") < out.index("low")


def test_second_submission_of_same_text_has_no_round(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    first = shown(step, capsys, "s1", "--stage", "plan")
    assert (first["round"], first["scoped"], first["stop"]) == (1, False, None)
    assert Path(first["target"]).read_text() == PLAN
    record_round(step, tmp_path, raw(severity="P1"))
    submit(store, tmp_path, repo)
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] == "unchanged"
    with pytest.raises(Refusal, match="unchanged"):
        record_round(step, tmp_path, raw(severity="P1", summary="Another problem."))
    assert [r.subject for r in rounds(store)] == ["s1:plan:1"]


def test_revised_plan_round_is_scoped_to_its_diff(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    record_round(step, tmp_path, raw(severity="P1"))
    submit(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    found = shown(step, capsys, "s1", "--stage", "plan")
    assert (found["round"], found["scoped"]) == (2, True)
    assert "+- [ ] send the receipt" in found["diff"]
    assert "-# Plan" not in found["diff"]


def test_lenses_resolve_to_files_and_a_missing_project_lens_refuses(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    lenses = shown(step, capsys, "s1", "--stage", "plan")["lenses"]
    assert [lens["name"] for lens in lenses] == ["contract", "correctness", "red-team", "tests", "input-shape"]
    assert all(Path(lens["path"]).is_file() for lens in lenses)
    (repo / ".claude" / "deckhand.toml").write_text('repository_label = "widgets"\nextra_lenses = {plan = ["money"]}\n')
    with pytest.raises(Refusal, match="the lens money has no file"):
        step.context(["s1", "--stage", "plan", *TEAM])
    (repo / ".claude" / "deckhand" / "lenses").mkdir(parents=True)
    (repo / ".claude" / "deckhand" / "lenses" / "money.md").write_text("# Money\n")
    assert shown(step, capsys, "s1", "--stage", "plan")["lenses"][-1]["path"].endswith("money.md")


def test_clean_round_says_nothing_found(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    record_round(step, tmp_path)
    step.context(["s1", "--stage", "plan", *TEAM])
    out = capsys.readouterr().out
    assert "Nothing found." in out
    assert "clean" in out


class BranchGit:
    def __init__(self, head: str, diffs: dict[str, str], ancestor: bool = True) -> None:
        self.head = head
        self.diffs = diffs
        self.ancestor = ancestor
        self.argvs: list[list[str]] = []
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, argv, cwd=None, stdin=None):
        self.argvs.append(argv)
        self.calls.append((argv, cwd))
        out = {"merge-base": "base1\n", "rev-parse": f"{self.head}\n", "merge-tree": "tree1\n"}.get(argv[1], "")
        if argv[1] == "diff":
            out = self.diffs[" ".join(argv[2:])]
        code = int(argv[2:3] == ["--is-ancestor"] and not self.ancestor)
        return subprocess.CompletedProcess(argv, code, out, "")


def test_branch_revision_is_scoped_to_the_new_commits(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    worktree(store, repo)
    git = BranchGit("c1", {"base1...HEAD": "+one\n"})
    checked(store, "c1")
    record_round(Review(store, git, repo), tmp_path, raw(severity="P1"), stage="branch")
    [held] = rounds(store)
    assert (held.subject, held.data["head"], held.version) == ("s1:branch:1", "c1", review.content_hash("+one\n"))
    git = BranchGit("c2", {"base1...HEAD": "+one\n+two\n", "c1..HEAD": "+two\n"})
    checked(store, "c2")
    found = shown(Review(store, git, repo), capsys, "s1", "--stage", "branch")
    assert (found["round"], found["scoped"], found["diff"]) == (2, True, None)
    assert Path(found["target"]).read_text() == "+two\n"
    assert ["git", "diff", "c1..HEAD"] in git.argvs


def test_branch_revision_after_a_sync_leaves_out_the_merged_base(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    worktree(store, repo)
    checked(store, "c1")
    record_round(Review(store, BranchGit("c1", {"base1...HEAD": "+one\n"}), repo), tmp_path, raw(), stage="branch")
    checked(store, "c3")
    git = BranchGit("c3", {"base1...HEAD": "+one\n+fix\n", "tree1 HEAD": "+fix\n"}, ancestor=False)
    found = shown(Review(store, git, repo), capsys, "s1", "--stage", "branch")
    assert (found["round"], Path(found["target"]).read_text()) == (2, "+fix\n")
    assert ["git", "merge-base", "--is-ancestor", "base1", "c1"] in git.argvs
    assert ["git", "merge-tree", "--write-tree", "c1", "base1"] in git.argvs


def test_a_sync_alone_carries_the_last_round_at_the_new_head(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    worktree(store, repo)
    checked(store, "c1")
    record_round(
        Review(store, BranchGit("c1", {"base1...HEAD": "+one\n"}), repo), tmp_path, raw(severity="P1"), stage="branch"
    )
    checked(store, "c2")
    git = BranchGit("c2", {"base1...HEAD": " sibling\n+one\n", "tree1 HEAD": ""}, ancestor=False)
    step = Review(store, git, repo)
    found = shown(step, capsys, "s1", "--stage", "branch")
    assert (found["stop"], found["round"], found["lenses"]) == ("carry", 2, [])
    with pytest.raises(Refusal, match="carries the last round forward"):
        record_round(step, tmp_path, raw(), stage="branch", target=found["target_hash"])
    step.apply(["s1", "round", "--stage", "branch", *TEAM, "--target", found["target_hash"]])
    held = rounds(store)[-1]
    assert (held.subject, held.data["carry"], held.data["head"]) == ("s1:branch:2", True, "c2")
    assert held.data["target_hash"] == found["target_hash"]
    assert shown(step, capsys, "s1", "--stage", "branch")["stop"] == "unchanged"


def test_branch_context_without_passing_checks_stops(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    worktree(store, repo)
    step = Review(store, BranchGit("c1", {"base1...HEAD": "+one\n"}), repo)
    capsys.readouterr()
    step.context(["s1", "--stage", "branch", *TEAM])
    assert "stop, checks: checks have not passed at this commit; run them first" in capsys.readouterr().out
    found = shown(step, capsys, "s1", "--stage", "branch")
    assert (found["stop"], found["round"], found["lenses"], found["target"]) == ("checks", None, [], None)
    before = list(store.writes)
    with pytest.raises(Refusal, match="no passing checks"):
        record_round(step, tmp_path, raw(), stage="branch", target=found["target_hash"])
    assert store.writes == before


def feature_ready() -> FakeStore:
    store = setup()
    mapped(store, "s1")
    mapped(store, "s2")
    return store


def test_the_feature_review_target_is_plain_and_its_hash_raw(state: Path, capsys: pytest.CaptureFixture[str]):
    store = feature_ready()
    planning.save_section(store, load(store), "Contract", "Reads [a.io](<http://a.io>) and x\\_y.", False)
    first = shown(FeatureReview(store), capsys)
    text = Path(first["target"]).read_text()
    assert "Reads a.io and x_y." in text
    assert first["target_hash"] == review.content_hash(features.review_text(store, load(store)) or "")


def test_feature_review_p1_is_an_approval_gap(tmp_path: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = feature_ready()
    step = FeatureReview(store)
    first = shown(step, capsys)
    assert [lens["name"] for lens in first["lenses"]] == ["contract"]
    assert "Orders" in Path(first["target"]).read_text()
    p1 = raw(lens="contract", severity="P1", summary="Receipts read a field orders never write.")
    record_round(step, tmp_path, p1)
    assert [r.subject for r in rounds(store, "s2", P1)] == [f"{P1}:feature:1"]
    gap = f"the feature review holds an open P1 {finding_id(p1, 'feature')}: Receipts read a field orders never write."
    assert features.approval_gaps(store, load(store)) == [gap]
    with pytest.raises(Refusal, match="not ready for approval"):
        Decide(store).context(["feature-approval", P1, *TEAM])
    answer(state, Decide(store), capsys, ["finding", "ACME-2", "--finding", finding_id(p1, "feature")], "Decline")
    assert features.approval_gaps(store, load(store)) == []


def test_feature_review_refuses_after_approval(tmp_path: Path):
    store = setup()
    approve_everything(store)
    with pytest.raises(Refusal, match="building"):
        FeatureReview(store).apply(["round", *TEAM, "--findings", findings_file(tmp_path), "--target", "x"])
    assert rounds(store) == []


def test_a_feature_round_runs_while_reapproval_is_due(tmp_path: Path):
    store = setup()
    approve_everything(store)
    planning.save_section(store, load(store), "Contract", "POST /orders returns 202.", False)
    assert features.phase(load(store)) is features.Phase.BUILDING
    stale = "the feature review has not reviewed the current contract and stories; run a feature round"
    assert features.approval_gaps(store, load(store)) == [stale]
    record_round(FeatureReview(store), tmp_path)
    assert features.approval_gaps(store, load(store)) == []


def test_agent_accept_cannot_replace_the_person_decline(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    p1 = raw(severity="P1")
    record_round(step, tmp_path, p1)
    answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", finding_id(p1)], "Decline")
    before = list(store.writes)
    with pytest.raises(Refusal, match="the person decided"):
        step.apply(["s1", "resolve", "--stage", "plan", *TEAM, "--accept", finding_id(p1)])
    assert store.writes == before
    assert review.declined(store.records("s1")) == {finding_id(p1)}


def test_plan_revised_after_context_refuses_the_round(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    seen = target_of(step)
    submit(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    before = list(store.writes)
    with pytest.raises(Refusal, match="changed since context"):
        record_round(step, tmp_path, raw(), target=seen)
    assert store.writes == before


def test_branch_commit_after_context_refuses_the_round(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    worktree(store, repo)
    checked(store, "c1")
    seen = target_of(Review(store, BranchGit("c1", {"base1...HEAD": "+one\n"}), repo), stage="branch")
    checked(store, "c2")
    moved = Review(store, BranchGit("c2", {"base1...HEAD": "+one\n+two\n"}), repo)
    before = list(store.writes)
    with pytest.raises(Refusal, match="changed since context"):
        record_round(moved, tmp_path, raw(), stage="branch", target=seen)
    assert store.writes == before


def test_the_feature_cap_is_the_persons_to_decide(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    store = feature_ready()
    step = FeatureReview(store)
    for n in (1, 2, 3, 4):
        planning.save_section(store, load(store), "Contract", f"POST /orders returns 20{n}.", False)
        if n < 4:
            record_round(step, tmp_path, raw(severity="P1", summary=f"Hole {n}."))
    capsys.readouterr()
    step.context([*TEAM])
    out = capsys.readouterr().out
    assert "stop, cap: three rounds left a P1 open; the person decides whether to proceed or rework" in out


def test_feature_review_is_mandatory_until_a_round_runs(tmp_path: Path):
    store = feature_ready()
    assert features.approval_gaps(store, load(store)) == ["the feature review has not run"]
    record_round(FeatureReview(store), tmp_path)
    assert features.approval_gaps(store, load(store)) == []
    planning.save_section(store, load(store), "Contract", "POST /orders returns 202.", False)
    assert features.approval_gaps(store, load(store)) == [
        "the feature review has not reviewed the current contract and stories; run a feature round"
    ]
    record_round(FeatureReview(store), tmp_path)
    assert features.approval_gaps(store, load(store)) == []


def test_feature_resolve_without_live_stories_refuses():
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    with pytest.raises(Refusal, match="no stories"):
        FeatureReview(store).apply(["resolve", *TEAM, "--accept", "000000000000"])
    assert store.writes[-1][0] == "set_project_dates"


def test_branch_round_without_a_head_reviews_the_whole_diff(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    plan = review.RoundPlan(1, scoped=False, base_hash="")
    found = review.parse_findings(json.dumps([raw(severity="P1")]))
    store.put_record("s1", review.round_record("s1", "branch", plan, "h1", found))
    checked(store, "c2")
    worktree(store, repo)
    step = Review(store, BranchGit("c2", {"base1...HEAD": "+one\n+two\n"}), repo)
    facts = shown(step, capsys, "s1", "--stage", "branch")
    assert (facts["round"], facts["scoped"]) == (2, True)
    assert Path(facts["target"]).read_text() == "+one\n+two\n"
    assert "holds no head" in facts["note"]


def test_scoped_round_carries_the_open_p1s(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    p1, p2 = raw(severity="P1"), raw(summary="Minor.", severity="P2")
    record_round(step, tmp_path, p1, p2)
    submit(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    facts = shown(step, capsys, "s1", "--stage", "plan")
    assert facts["scoped"] is True
    assert [(c["id"], c["summary"]) for c in facts["carried"]] == [(finding_id(p1), p1["summary"])]
    capsys.readouterr()
    step.context(["s1", "--stage", "plan", *TEAM])
    out = capsys.readouterr().out
    assert "re-report" in out and finding_id(p1) in out.split("re-report")[1]
    answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", finding_id(p1)], "Decline")
    facts = shown(step, capsys, "s1", "--stage", "plan")
    assert (facts["round"], facts["scoped"], facts["carried"]) == (2, True, [])


def test_branch_review_runs_from_the_clone_in_the_story_worktree(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    path = worktree(store, repo)
    checked(store, "c1")
    git = BranchGit("c1", {"base1...HEAD": "+one\n"})
    found = shown(Review(store, git, repo), capsys, "s1", "--stage", "branch")
    assert (found["round"], found["diff"], Path(found["target"]).read_text()) == (1, None, "+one\n")
    assert {cwd for _, cwd in git.calls} == {path}


def test_branch_review_without_a_worktree_refuses(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    checked(store, "c1")
    with pytest.raises(Refusal, match="has no worktree"):
        Review(store, BranchGit("c1", {"base1...HEAD": "+one\n"}), repo).context(["s1", "--stage", "branch", *TEAM])


def test_proceed_at_the_cap_stops_proceeded_and_a_revision_still_gets_a_round(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    for n in (1, 2, 3):
        if n > 1:
            submit(store, tmp_path, repo, PLAN + f"\nRevision {n}.\n")
        record_round(step, tmp_path, raw(severity="P1", summary=f"Gap {n}."))
    submit(store, tmp_path, repo, PLAN + "\nRevision 4.\n")
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] == "cap"
    submit(store, tmp_path, repo, PLAN + "\nRevision 3.\n")
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] == "unchanged"
    answer(state, Decide(store), capsys, ["review-cap", "ACME-1", "--stage", "plan"], "Proceed")
    facts = shown(step, capsys, "s1", "--stage", "plan")
    assert facts["stop"] == "proceeded"
    assert [f["status"] for f in facts["last_round"]["findings"]] == ["waived by the person"]
    submit(store, tmp_path, repo, PLAN + "\nRevision 4.\n")
    facts = shown(step, capsys, "s1", "--stage", "plan")
    assert (facts["stop"], facts["round"], facts["scoped"], facts["carried"]) == (None, 4, True, [])
    record_round(step, tmp_path, raw(severity="P1", summary="A new hole."))
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] == "unchanged"


def capped_twice(tmp_path: Path, repo: Path, state: Path, capsys, first: str) -> FakeStore:
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    for n in range(1, 7):
        if n > 1:
            submit(store, tmp_path, repo, PLAN + f"\nRevision {n}.\n")
        left = [raw(summary="A name is vague.")] if n == 1 else []
        record_round(step, tmp_path, raw(severity="P1", summary=f"Gap {n}."), *left)
        if n in (3, 6):
            choice = first if n == 3 else "Proceed"
            answer(state, Decide(store), capsys, ["review-cap", "ACME-1", "--stage", "plan"], choice)
    assert len([r for r in store.records("s1") if r.kind == review.CAP_DECISION]) == 2
    return store


def test_a_proceed_after_a_rework_keeps_the_rework_cut(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    records = capped_twice(tmp_path, repo, state, capsys, "Rework").records("s1")
    assert review.counted(records, "s1:plan")[1] == 3
    assert review.waived(records, "s1:plan") == {finding_id(raw(severity="P1", summary="Gap 6."))}
    assert review_gaps.left_open(records, "s1", "plan") == []


def test_a_second_proceed_keeps_the_first_waiver(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    records = capped_twice(tmp_path, repo, state, capsys, "Proceed").records("s1")
    gaps = {finding_id(raw(severity="P1", summary=f"Gap {n}.")) for n in (3, 6)}
    assert review.waived(records, "s1:plan") == gaps


def test_rounds_past_three_without_a_p1_keep_reviewing_and_never_print_cap_clear(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    for n in (1, 2, 3):
        record_round(step, tmp_path, raw(severity="P2", summary=f"Gap {n}."))
        submit(store, tmp_path, repo, PLAN + f"\nRevision {n}.\n")
    capsys.readouterr()
    step.context(["s1", "--stage", "plan", *TEAM])
    out = capsys.readouterr().out
    assert "round 4, scoped to the revision" in out and "cap-clear" not in out
    record_round(step, tmp_path)
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] == "clean"


def test_a_plan_of_another_repository_refuses_to_save(tmp_path: Path, repo: Path):
    store = setup()
    approve_everything(store)
    store.set_labels("s1", ["gadgets"])
    reapprove(store)
    with pytest.raises(Refusal, match="another repository"):
        submit(store, tmp_path, repo)
    assert [r for r in store.records("s1") if r.kind == "implplan"] == []


def test_a_scoped_round_carries_accepted_findings_and_records_what_it_carried(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    p1, p2 = raw(severity="P1"), raw(summary="Minor.", severity="P2")
    record_round(step, tmp_path, p1, p2)
    answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", finding_id(p2)], "Accept")
    submit(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    facts = shown(step, capsys, "s1", "--stage", "plan")
    assert [c["id"] for c in facts["carried"]] == [finding_id(p1), finding_id(p2)]
    record_round(step, tmp_path, p1)
    latest = max(rounds(store), key=lambda r: r.subject)
    assert latest.data["carried"] == [finding_id(p1), finding_id(p2)]
    assert review.unfixed(store.records("s1")) == []


def test_plan_and_branch_context_carry_the_approved_spec(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    decided(store, "decision-item", "Greeting language", "English only", ["s1"])
    step = Review(store, Git(BASE), repo)
    plan = shown(step, capsys, "s1", "--stage", "plan")
    held = Path(plan["spec"]).read_text()
    assert "The Contract of Alpha." in held
    assert "- Greeting language: English only" in held
    assert plan["spec"] != plan["target"]
    step.context(["s1", "--stage", "plan", *TEAM])
    out = capsys.readouterr().out
    assert out.index(f"target: {plan['target']}") < out.index(f"spec: {plan['spec']}")
    worktree(store, repo)
    checked(store, "c1")
    branch = shown(Review(store, BranchGit("c1", {"base1...HEAD": "+one\n"}), repo), capsys, "s1", "--stage", "branch")
    assert Path(branch["spec"]).read_text() == held


def test_a_contract_edit_after_a_round_needs_no_new_round(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]
):
    store = planned(tmp_path, repo)
    worktree(store, repo)
    checked(store, "c1")
    git = BranchGit("c1", {"base1...HEAD": "+one\n"})
    plan, branch = Review(store, Git(BASE), repo), Review(store, git, repo)
    record_round(plan, tmp_path)
    record_round(branch, tmp_path, stage="branch")
    before = [shown(plan, capsys, "s1", "--stage", "plan"), shown(branch, capsys, "s1", "--stage", "branch")]
    planning.save_section(store, load(store), "Contract", "POST /orders returns 202.", False)
    approve_everything(store)
    after = [shown(plan, capsys, "s1", "--stage", "plan"), shown(branch, capsys, "s1", "--stage", "branch")]
    assert after == before
    assert [facts["stop"] for facts in after] == ["clean", "clean"]


def test_feature_review_has_no_spec(capsys: pytest.CaptureFixture[str]):
    step = FeatureReview(feature_ready())
    facts = shown(step, capsys)
    assert (facts["round"], facts["spec"]) == (1, None)
    step.context([*TEAM])
    assert "spec:" not in capsys.readouterr().out
