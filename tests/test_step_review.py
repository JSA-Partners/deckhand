from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from deckhand import answers, features, planning, review
from deckhand.features import FEATURE_LABEL
from deckhand.step import Refusal
from deckhand.steps.decide import Decide
from deckhand.steps.implplan import ImplPlan
from deckhand.steps.review import FeatureReview, Review
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import P1, approve_everything, decided, load, sat, setup
from tests.test_implplan import BASE, Git, manifest

TEAM = ["--team-key", "ACME"]
PLAN = "# Plan\n\n- [ ] queue the order\n"


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    found = tmp_path / "repo"
    (found / ".claude").mkdir(parents=True)
    (found / ".claude" / "deckhand.toml").write_text('repository_label = "widgets"\n')
    return found


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


def finding_id(item: dict) -> str:
    return review.parse_findings(json.dumps([item]))[0].id


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
    with pytest.raises(Refusal, match="only the person declines a finding"):
        step.apply([*resolve, "--decline", finding_id(p1)])
    with pytest.raises(Refusal, match="changes what the story delivers"):
        step.apply([*resolve, "--accept", finding_id(p1), "--accept", finding_id(delivery)])
    with pytest.raises(Refusal, match="not a finding of the last round"):
        step.apply([*resolve, "--accept", "000000000000"])
    assert store.writes == before
    step.apply([*resolve, "--accept", finding_id(p1)])
    [accept] = [r for r in store.records("s1") if r.kind == review.HAND_ACCEPT]
    assert (accept.subject, accept.author, accept.data["answer"]) == (finding_id(p1), "agent:review", "Accept")
    assert review.declined(store.records("s1")) == set()


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
    submit(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] == "clean"


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
    submit(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    step.context(["s1", "--stage", "plan", *TEAM])
    out = capsys.readouterr().out
    assert "Nothing found." in out
    assert "clean" in out


class BranchGit:
    def __init__(self, head: str, diffs: dict[str, str]) -> None:
        self.head = head
        self.diffs = diffs
        self.argvs: list[list[str]] = []

    def __call__(self, argv, cwd=None, stdin=None):
        self.argvs.append(argv)
        out = {"merge-base": "base1\n", "rev-parse": f"{self.head}\n"}.get(argv[1], "")
        if argv[1] == "diff":
            out = self.diffs[argv[2]]
        return subprocess.CompletedProcess(argv, 0, out, "")


def test_branch_revision_is_scoped_to_the_new_commits(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    git = BranchGit("c1", {"base1...HEAD": "+one\n"})
    record_round(Review(store, git, repo), tmp_path, raw(severity="P1"), stage="branch")
    [held] = rounds(store)
    assert (held.subject, held.data["head"], held.version) == ("s1:branch:1", "c1", review.content_hash("+one\n"))
    git = BranchGit("c2", {"base1...HEAD": "+one\n+two\n", "c1..HEAD": "+two\n"})
    found = shown(Review(store, git, repo), capsys, "s1", "--stage", "branch")
    assert (found["round"], found["scoped"], found["diff"]) == (2, True, "+two\n")
    assert ["git", "diff", "c1..HEAD"] in git.argvs


def feature_ready() -> FakeStore:
    store = setup()
    decided(store, "contract", P1, "Approve", ["s1", "s2"])
    decided(store, "invariants", P1, "Approve", ["s1", "s2"])
    sat(store, "s1")
    sat(store, "s2")
    return store


def test_feature_review_p1_is_an_approval_gap(tmp_path: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = feature_ready()
    step = FeatureReview(store)
    first = shown(step, capsys)
    assert [lens["name"] for lens in first["lenses"]] == ["contract"]
    assert "Orders" in Path(first["target"]).read_text()
    p1 = raw(lens="contract", severity="P1", summary="Receipts read a field orders never write.")
    record_round(step, tmp_path, p1)
    assert [r.subject for r in rounds(store, "s2", P1)] == [f"{P1}:feature:1"]
    gap = f"the feature review holds an open P1 {finding_id(p1)}: Receipts read a field orders never write."
    assert features.approval_gaps(store, load(store)) == [gap]
    with pytest.raises(Refusal, match="not ready for approval"):
        Decide(store).context(["feature-approval", P1, *TEAM])
    answer(state, Decide(store), capsys, ["finding", "ACME-2", "--finding", finding_id(p1)], "Decline")
    assert features.approval_gaps(store, load(store)) == []


def test_feature_review_refuses_after_approval(tmp_path: Path):
    store = setup()
    approve_everything(store)
    with pytest.raises(Refusal, match="building"):
        FeatureReview(store).apply(["round", *TEAM, "--findings", findings_file(tmp_path), "--target", "x"])
    assert rounds(store) == []


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
    seen = target_of(Review(store, BranchGit("c1", {"base1...HEAD": "+one\n"}), repo), stage="branch")
    moved = Review(store, BranchGit("c2", {"base1...HEAD": "+one\n+two\n"}), repo)
    before = list(store.writes)
    with pytest.raises(Refusal, match="changed since context"):
        record_round(moved, tmp_path, raw(), stage="branch", target=seen)
    assert store.writes == before


def test_feature_review_is_mandatory_until_a_round_runs(tmp_path: Path):
    store = feature_ready()
    assert features.approval_gaps(store, load(store)) == ["the feature review has not run"]
    record_round(FeatureReview(store), tmp_path)
    assert features.approval_gaps(store, load(store)) == []
    planning.save_section(store, load(store), "Contract", "POST /orders returns 202.", False)
    decided(store, "contract", P1, "Approve", ["s1", "s2"])
    assert features.approval_gaps(store, load(store)) == []


def test_feature_resolve_without_live_stories_refuses():
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    with pytest.raises(Refusal, match="no stories"):
        FeatureReview(store).apply(["resolve", *TEAM, "--accept", "000000000000"])
    assert store.writes[-1][0] == "set_project_dates"


def test_branch_round_without_a_head_refuses(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    plan = review.RoundPlan(1, scoped=False, base_hash="")
    found = review.parse_findings(json.dumps([raw(severity="P1")]))
    store.put_record("s1", review.round_record("s1", "branch", plan, "h1", found))
    step = Review(store, BranchGit("c2", {"base1...HEAD": "+two\n"}), repo)
    with pytest.raises(Refusal, match="holds no head"):
        step.context(["s1", "--stage", "branch", *TEAM])


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
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] == "clean"
