from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand import review
from deckhand.step import Refusal
from deckhand.steps.build import Build
from deckhand.steps.decide import Decide
from deckhand.steps.review import Review
from deckhand.store.fake import FakeStore
from tests.test_implplan import BASE, Git
from tests.test_step_review import (
    PLAN,
    TEAM,
    answer,
    planned,
    raw,
    record_round,
    repo,
    shown,
    state,
    submit,
)

__all__ = ["repo", "state"]

BRANCH = "acme-1-orders"


class Shell:
    def __init__(self, head: str = "c2", base: str = "c1", origin: bool = True) -> None:
        self.head, self.base, self.origin = head, base, origin
        self.argvs: list[list[str]] = []
        self.created = False

    def __call__(self, argv, cwd=None, stdin=None):
        self.argvs.append(argv)
        out, code = "", 0
        if argv[:2] == ["git", "rev-parse"] and "--verify" in argv:
            code = 0 if self.origin else 128
        elif argv[:2] == ["git", "rev-parse"]:
            out = f"{self.head}\n"
        elif argv[:2] == ["git", "merge-base"]:
            out = f"{self.base}\n"
        elif argv[:2] == ["git", "show-ref"]:
            code = 1
        elif argv[:3] == ["gh", "repo", "view"]:
            out = json.dumps({"nameWithOwner": "acme/widgets"})
        elif argv[:3] == ["gh", "pr", "create"]:
            self.created = True
        elif argv[:3] == ["gh", "pr", "list"]:
            found = [{"number": 7, "url": "https://example.test/pull/7", "isDraft": True, "headRefOid": "c2"}]
            out = json.dumps(found if self.created else [])
        return subprocess.CompletedProcess(argv, code, out, "")

    def ran(self, *prefix: str) -> list[list[str]]:
        return [a for a in self.argvs if a[: len(prefix)] == list(prefix)]


def labeled(tmp_path: Path, repo: Path) -> FakeStore:
    store = planned(tmp_path, repo)
    store.set_labels("s1", ["widgets"])
    (repo / ".worktrees" / BRANCH).mkdir(parents=True)
    return store


def round_of(step: Review, tmp_path: Path, n: int, *items: dict) -> None:
    submit(step._store, tmp_path, step._repo, PLAN + f"- [ ] step {n}\n")
    record_round(step, tmp_path, *items)


def to_cap(store: FakeStore, tmp_path: Path, repo: Path) -> Review:
    step = Review(store, Git(BASE), repo)
    record_round(step, tmp_path, raw(severity="P1"))
    for n in (2, 3):
        round_of(step, tmp_path, n, raw(severity="P1"))
    return step


def push(store: FakeStore, shell: Shell, repo: Path) -> None:
    Build(store, shell, repo).apply(["s1", "push", *TEAM])


def test_push_refuses_before_plan_review_clean(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    shell = Shell()
    with pytest.raises(Refusal, match="has not stopped clean"):
        push(store, shell, repo)
    record_round(Review(store, Git(BASE), repo), tmp_path, raw(severity="P1"))
    with pytest.raises(Refusal, match="has not stopped clean"):
        push(store, shell, repo)
    assert shell.ran("git", "push") == [] and shell.ran("gh", "pr", "create") == []


def test_push_opens_one_draft_pr(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    record_round(Review(store, Git(BASE), repo), tmp_path)
    shell = Shell()
    push(store, shell, repo)
    push(store, shell, repo)
    [create] = shell.ran("gh", "pr", "create")
    assert create[create.index("--title") + 1] == "ACME-1 Orders"
    assert "--draft" in create and create[create.index("--head") + 1] == BRANCH
    assert create[create.index("--repo") + 1] == "acme/widgets"
    assert len(shell.ran("git", "push")) == 2
    assert "https://example.test/pull/7" in capsys.readouterr().out


def test_push_without_commits_refuses(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    record_round(Review(store, Git(BASE), repo), tmp_path)
    shell = Shell(head="c1", base="c1")
    with pytest.raises(Refusal, match="no commits"):
        push(store, shell, repo)
    assert shell.ran("git", "push") == [] and shell.ran("gh", "pr", "create") == []


def test_proceed_at_the_cap_opens_the_gate(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    to_cap(store, tmp_path, repo)
    with pytest.raises(Refusal, match="has not stopped clean"):
        push(store, Shell(), repo)
    question = answer(state, Decide(store), capsys, ["review-cap", "ACME-1"], "Proceed")
    assert [o["label"] for o in question["options"]] == ["Proceed", "Rework the plan"]
    assert "recommended" not in json.dumps(question).lower()
    shell = Shell()
    push(store, shell, repo)
    assert len(shell.ran("gh", "pr", "create")) == 1


def test_cap_decision_refuses_before_the_cap(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    record_round(Review(store, Git(BASE), repo), tmp_path, raw(severity="P1"))
    with pytest.raises(Refusal, match="not reached its cap"):
        Decide(store).context(["review-cap", "ACME-1", *TEAM])


def test_rework_restarts_the_round_count(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    step = to_cap(store, tmp_path, repo)
    answer(state, Decide(store), capsys, ["review-cap", "ACME-1"], "Rework the plan")
    submit(store, tmp_path, repo, PLAN + "- [ ] reworked\n")
    facts = shown(step, capsys, "s1", "--stage", "plan")
    assert (facts["stop"], facts["round"], facts["scoped"]) == (None, 4, False)
    record_round(step, tmp_path, raw(severity="P1", summary="Still wrong."))
    assert [r.subject for r in store.records("s1") if r.kind == review.KIND][-1] == "s1:plan:4"
    with pytest.raises(Refusal, match="has not stopped clean"):
        push(store, Shell(), repo)
    round_of(step, tmp_path, 5)
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] == "unchanged"
    push(store, Shell(), repo)


def test_worktree_is_made_from_origin_head_once(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = planned(tmp_path, repo)
    store.set_labels("s1", ["widgets"])
    shell = Shell()
    Build(store, shell, repo).apply(["s1", "worktree", *TEAM])
    [add] = shell.ran("git", "worktree", "add")
    assert add[-2:] == [str(repo / ".worktrees" / BRANCH), "origin/HEAD"]
    assert add[add.index("-b") + 1] == BRANCH
    (repo / ".worktrees" / BRANCH).mkdir(parents=True)
    Build(store, shell, repo).apply(["s1", "worktree", *TEAM])
    assert len(shell.ran("git", "worktree", "add")) == 1


def test_wrong_place_run_refuses(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    store.set_labels("s1", ["gadgets"])
    shell = Shell()
    with pytest.raises(Refusal, match="another repository"):
        Build(store, shell, repo).apply(["s1", "worktree", *TEAM])
    assert shell.ran("git", "worktree") == []


def test_missing_origin_head_refuses(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    store.set_labels("s1", ["widgets"])
    with pytest.raises(Refusal, match="remote set-head"):
        Build(store, Shell(origin=False), repo).apply(["s1", "worktree", *TEAM])


def test_context_prints_the_briefing(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    (repo / ".claude" / "deckhand.toml").write_text(
        'repository_label = "widgets"\nchecks = ["make lint"]\n[tier_tools]\n"0" = ["ruff"]\n'
    )
    capsys.readouterr()
    Build(store, Shell(), repo).context(["s1", *TEAM, "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert (facts["story"], facts["tier"], facts["tools"], facts["checks"]) == ("ACME-1", 0, ["ruff"], ["make lint"])
    assert facts["worktree"] == str(repo / ".worktrees" / BRANCH)
    assert facts["plan"] == PLAN
