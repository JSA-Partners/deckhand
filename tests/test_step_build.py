from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from deckhand import git, launch, ops, review, spec, step
from deckhand.step import Refusal
from deckhand.steps.build import Build
from deckhand.steps.commit import Commit
from deckhand.steps.decide import Decide
from deckhand.steps.implplan import ImplPlan
from deckhand.steps.review import Review
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import approve_everything, decided, setup
from tests.test_implplan import BASE, Git, manifest
from tests.test_step_review import (
    PLAN,
    TEAM,
    answer,
    finding_id,
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
    def __init__(self, head: str = "c2", base: str = "c1", origin: bool = True, dirty: bool = False) -> None:
        self.head, self.base, self.origin, self.dirty = head, base, origin, dirty
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
        elif argv[:2] == ["git", "status"]:
            out = " M a.py\n" if self.dirty else ""
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
    Build(store, Shell(), repo).apply(["s1", "worktree", *TEAM])
    (repo / ".worktrees" / BRANCH).mkdir(parents=True)
    return store


def round_of(step: Review, tmp_path: Path, n: int, *items: dict) -> None:
    submit(step._store_for_run(), tmp_path, step._repo, PLAN + f"- [ ] step {n}\n")
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


def test_plan_revised_after_a_clean_round_needs_a_review(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    step = Review(store, Git(BASE), repo)
    record_round(step, tmp_path)
    submit(store, tmp_path, repo, PLAN + "- [ ] send the receipt\n")
    with pytest.raises(Refusal, match="has not stopped clean"):
        push(store, Shell(), repo)
    record_round(step, tmp_path)
    push(store, Shell(), repo)


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
    assert [o["label"] for o in question["options"]] == ["Proceed", "Rework"]
    assert "recommended" not in json.dumps(question).lower()
    shell = Shell()
    push(store, shell, repo)
    assert len(shell.ran("gh", "pr", "create")) == 1


def test_proceed_does_not_cover_a_later_plan_revision(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    step = to_cap(store, tmp_path, repo)
    answer(state, Decide(store), capsys, ["review-cap", "ACME-1"], "Proceed")
    submit(store, tmp_path, repo, PLAN + "- [ ] revised after the cap\n")
    shell = Shell()
    with pytest.raises(Refusal, match="has not stopped clean"):
        push(store, shell, repo)
    assert shell.ran("git", "push") == []
    with pytest.raises(Refusal, match="not reached its cap"):
        Decide(store).context(["review-cap", "ACME-1", *TEAM])
    record_round(step, tmp_path)
    push(store, Shell(), repo)


def test_cap_decision_refuses_before_the_cap(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    record_round(Review(store, Git(BASE), repo), tmp_path, raw(severity="P1"))
    with pytest.raises(Refusal, match="not reached its cap"):
        Decide(store).context(["review-cap", "ACME-1", *TEAM])


def test_rework_restarts_the_round_count(tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    step = to_cap(store, tmp_path, repo)
    answer(state, Decide(store), capsys, ["review-cap", "ACME-1"], "Rework")
    submit(store, tmp_path, repo, PLAN + "- [ ] reworked\n")
    facts = shown(step, capsys, "s1", "--stage", "plan")
    assert (facts["stop"], facts["round"], facts["scoped"]) == (None, 4, False)
    record_round(step, tmp_path, raw(severity="P1", summary="Still wrong."))
    assert [r.subject for r in store.records("s1") if r.kind == review.KIND][-1] == "s1:plan:4"
    with pytest.raises(Refusal, match="has not stopped clean"):
        push(store, Shell(), repo)
    round_of(step, tmp_path, 5)
    assert shown(step, capsys, "s1", "--stage", "plan")["stop"] == "clean"
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


def test_build_context_prints_the_spec_before_the_plan(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    capsys.readouterr()
    Build(store, Shell(), repo).context(["s1", *TEAM])
    out = capsys.readouterr().out
    contract = "# Contract\n\nWritten by the person.\n\nThe Contract of Alpha."
    assert contract in out
    assert out.index(contract) < out.index("# Decisions") < out.index("plan:\n" + PLAN)
    Build(store, Shell(), repo).context(["s1", *TEAM, "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert facts["contract"] == "Written by the person.\n\nThe Contract of Alpha."
    assert facts["examples"] == "Written by the person.\n\nThe Examples of Alpha."
    assert facts["invariants"] == "Written by the person.\n\nThe Invariants of Alpha."
    assert facts["decisions"] == []


def test_a_long_context_prints_the_spec_and_saves_the_whole(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    store = labeled(tmp_path, repo)
    store.put_document(ops.create_id("s1", "sitting"), "s1", "Sitting", "An order of two ships the same day.\n" * 600)
    capsys.readouterr()
    Build(store, Shell(), repo).context(["s1", *TEAM])
    out = capsys.readouterr().out
    saved = tmp_path / launch.INPUT / "ACME-1-build.md"
    assert len(out.encode("utf-8")) < spec.LIMIT
    assert "The Contract of Alpha." in out and str(saved) in out and "plan:" not in out
    assert "The Contract of Alpha." in saved.read_text() and "sitting:\nAn order of two" in saved.read_text()


def test_a_long_examples_section_leaves_the_contract_and_decisions_inline(
    tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    store = setup()
    decided(store, "decision-item", "Ids are UUIDs", "Keep", ["s1"])
    approve_everything(store)
    submit(store, tmp_path, repo)
    store.set_labels("s1", ["widgets"])
    Build(store, Shell(), repo).apply(["s1", "worktree", *TEAM])
    gather = spec.gather

    def long_examples(*args) -> spec.Spec:
        return dataclasses.replace(gather(*args), examples="An order of two ships the same day.\n" * 1000)

    monkeypatch.setattr(spec, "gather", long_examples)
    capsys.readouterr()
    Build(store, Shell(), repo).context(["s1", *TEAM])
    out = capsys.readouterr().out
    assert len(out.encode("utf-8")) < spec.LIMIT
    assert "The Contract of Alpha." in out and "- Ids are UUIDs: Keep" in out and "# Scope" not in out


def test_a_renamed_story_keeps_its_worktree(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    store.set_labels("s1", ["widgets"])
    shell = Shell()
    Build(store, shell, repo).apply(["s1", "worktree", *TEAM])
    (repo / ".worktrees" / BRANCH).mkdir(parents=True)
    record_round(Review(store, Git(BASE), repo), tmp_path)
    store._issues["s1"]["branch_name"] = "acme-1-orders-renamed"
    push(store, shell, repo)
    [create] = shell.ran("gh", "pr", "create")
    assert create[create.index("--head") + 1] == BRANCH
    assert [r.data for r in store.records("s1") if r.kind == "worktree"] == [{"branch": BRANCH}]


def test_push_without_a_worktree_record_refuses(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    store.set_labels("s1", ["widgets"])
    record_round(Review(store, Git(BASE), repo), tmp_path)
    with pytest.raises(Refusal, match="has no worktree"):
        push(store, Shell(), repo)


def test_push_waits_for_the_person_on_a_delivery_finding(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = labeled(tmp_path, repo)
    delivery = raw(changes_delivery=True)
    record_round(Review(store, Git(BASE), repo), tmp_path, delivery)
    shell = Shell()
    with pytest.raises(Refusal, match=f"{finding_id(delivery)} changes what the story delivers"):
        push(store, shell, repo)
    assert shell.ran("git", "push") == []
    answer(state, Decide(store), capsys, ["finding", "ACME-1", "--finding", finding_id(delivery)], "Accept")
    push(store, shell, repo)
    assert len(shell.ran("git", "push")) == 1


class Rooted(Shell):
    def __init__(self, clone: Path) -> None:
        super().__init__()
        self.clone = clone

    def __call__(self, argv, cwd=None, stdin=None):
        if "--git-common-dir" in argv:
            self.argvs.append(argv)
            return subprocess.CompletedProcess(argv, 0, f"{self.clone / '.git'}\n", "")
        return super().__call__(argv, cwd, stdin)


def test_locate_from_clone_and_worktree_agree(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch):
    store = labeled(tmp_path, repo)
    from_clone = Build(store, Shell(), repo).locate("s1", "ACME")
    worktree = repo / ".worktrees" / BRANCH
    monkeypatch.chdir(worktree)
    from_worktree = Build(store, Rooted(repo)).locate("s1", "ACME")
    assert (from_worktree.clone, from_worktree.path, from_worktree.head) == (repo, worktree, "c2")
    assert (from_clone.clone, from_clone.path, from_clone.head) == (repo, worktree, "c2")
    assert from_worktree.story["id"] == from_clone.story["id"] == "s1"


def test_locate_without_worktree_has_no_head(tmp_path: Path, repo: Path):
    store = planned(tmp_path, repo)
    store.set_labels("s1", ["widgets"])
    shell = Shell()
    found = Build(store, shell, repo).locate("s1", "ACME")
    assert (found.path, found.head) == (None, None)
    assert shell.ran("git", "rev-parse") == []


def test_a_plan_is_checked_at_the_story_worktree(tmp_path: Path, repo: Path):
    store = labeled(tmp_path, repo)
    git = Git(BASE)
    plan, found = tmp_path / "again.md", tmp_path / "again.json"
    plan.write_text(PLAN + "- [ ] more\n")
    found.write_text(json.dumps(manifest()))
    ImplPlan(store, git, repo).apply(["s1", *TEAM, "--plan", str(plan), "--manifest", str(found)])
    [base] = [cwd for argv, cwd in git.calls if argv[:2] == ["git", "merge-base"]]
    assert base == repo / ".worktrees" / BRANCH


def test_team_key_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "config.toml").write_text('team_key = "ACME"\n', encoding="utf-8")
    monkeypatch.setenv("DECKHAND_CONFIG_DIR", str(tmp_path))
    assert step.team_key({"words": []}) == "ACME"


def test_flag_overrides_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "config.toml").write_text('team_key = "ACME"\n', encoding="utf-8")
    monkeypatch.setenv("DECKHAND_CONFIG_DIR", str(tmp_path))
    assert step.team_key({"team_key": "OTHER"}) == "OTHER"


def test_missing_key_names_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DECKHAND_CONFIG_DIR", str(tmp_path))
    with pytest.raises(Refusal) as refused:
        step.team_key({"words": []})
    assert refused.value.rule == f"no team key: pass --team-key or set team_key in {tmp_path / 'config.toml'}"


def test_locate_story_of_second_repository(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch):
    store = labeled(tmp_path, repo)
    second = tmp_path / "gadgets"
    (second / ".claude").mkdir(parents=True)
    (second / ".claude" / "deckhand.toml").write_text('repository_label = "gadgets"\n', encoding="utf-8")
    store.set_labels("s1", ["gadgets"])
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(f'[clones]\ngadgets = "{second}"\n', encoding="utf-8")
    monkeypatch.setenv("DECKHAND_CONFIG_DIR", str(config_dir))
    found = Build(store, Shell(), repo).locate("s1", "ACME")
    assert (found.clone, found.policy.repository_label, found.path) == (second, "gadgets", None)


def test_a_hand_write_refuses_a_story_another_holds(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]):
    store = labeled(tmp_path, repo)
    store.assign("s1", "bob")
    store.writes.clear()
    shell = Shell()
    Build(store, shell, repo, me=lambda: "ann").context(["s1", *TEAM])
    assert "story: ACME-1" in capsys.readouterr().out
    with pytest.raises(Refusal, match="^ACME-1 belongs to bob$"):
        Build(store, shell, repo, me=lambda: "ann").apply(["s1", "push", *TEAM])
    assert store.writes == []
    assert shell.ran("git", "push") == []


def real(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeStore, Path]:
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "t")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@t")
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    sh(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\n")
    sh(repo, "add", "a.txt")
    sh(repo, "commit", "-qm", "chore: base")
    sh(repo, "remote", "add", "origin", str(origin))
    sh(repo, "push", "-q", "-u", "origin", "main")
    sh(repo, "remote", "set-head", "origin", "main")
    store = planned(tmp_path, repo)
    store.set_labels("s1", ["widgets"])
    Build(store, git.run, repo).apply(["s1", "worktree", *TEAM])
    return store, repo / ".worktrees" / BRANCH


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(store: FakeStore, repo: Path, tmp_path: Path, message: str, runner: git.Runner = git.run) -> None:
    path = tmp_path / "message.txt"
    path.write_text(message)
    Build(store, runner, repo).apply(["s1", "commit", "--message", str(path), *TEAM])


def test_commit_stages_and_commits(tmp_path: Path, repo: Path, monkeypatch, capsys: pytest.CaptureFixture[str]):
    store, worktree = real(tmp_path, repo, monkeypatch)
    (worktree / "a.txt").write_text("two\n")
    (worktree / "b.txt").write_text("new\n")
    capsys.readouterr()
    commit(store, repo, tmp_path, "feat: queue the order\n\nThe order waits in a queue.\n")
    head = sh(worktree, "rev-parse", "HEAD")
    assert sh(worktree, "log", "-1", "--format=%B") == "feat: queue the order\n\nThe order waits in a queue."
    assert sh(worktree, "status", "--porcelain") == ""
    assert sh(worktree, "show", "--name-only", "--format=", "HEAD").splitlines() == ["a.txt", "b.txt"]
    assert head in capsys.readouterr().out
    with pytest.raises(Refusal, match="nothing to commit"):
        commit(store, repo, tmp_path, "fix: again\n")


@pytest.mark.parametrize(
    "message",
    [
        "feat: queue the order\n\nCo-Authored-By: someone <s@example.test>\n",
        "queue the order\n",
        "feat: " + "x" * 66 + "\n",
        "feat: two\nlines\n",
        "",
    ],
)
def test_commit_refuses_trailer(tmp_path: Path, repo: Path, message: str):
    store = labeled(tmp_path, repo)
    shell = Shell(dirty=True)
    with pytest.raises(Refusal, match="conventional subject"):
        commit(store, repo, tmp_path, message, shell)
    assert shell.ran("git", "add") == [] and shell.ran("git", "commit") == []


def test_sync_conflict_leaves_markers(tmp_path: Path, repo: Path, monkeypatch):
    store, worktree = real(tmp_path, repo, monkeypatch)
    (worktree / "a.txt").write_text("story\n")
    commit(store, repo, tmp_path, "feat: story side\n")
    (repo / "a.txt").write_text("main\n")
    sh(repo, "commit", "-qam", "fix: main side")
    sh(repo, "push", "-q", "origin", "main")
    with pytest.raises(Refusal, match=r"conflicts in a\.txt"):
        Build(store, git.run, repo).apply(["s1", "sync", *TEAM])
    assert "<<<<<<<" in (worktree / "a.txt").read_text()
    with pytest.raises(Refusal, match="conflict markers remain in a.txt"):
        commit(store, repo, tmp_path, "fix: take both sides\n")
    (worktree / "a.txt").write_text("story\nmain\n")
    commit(store, repo, tmp_path, "fix: take both sides\n")
    assert len(sh(worktree, "log", "-1", "--format=%P").split()) == 2
    assert sh(worktree, "status", "--porcelain") == ""


def test_sync_merges_main_into_the_branch(tmp_path: Path, repo: Path, monkeypatch):
    store, worktree = real(tmp_path, repo, monkeypatch)
    (repo / "c.txt").write_text("main\n")
    sh(repo, "add", "c.txt")
    sh(repo, "commit", "-qm", "feat: main side")
    sh(repo, "push", "-q", "origin", "main")
    Build(store, git.run, repo).apply(["s1", "sync", *TEAM])
    assert (worktree / "c.txt").read_text() == "main\n"
    (worktree / "c.txt").write_text("dirty\n")
    with pytest.raises(Refusal, match="uncommitted"):
        Build(store, git.run, repo).apply(["s1", "sync", *TEAM])


def test_hand_role_ignores_working_directory_clone(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch):
    store = labeled(tmp_path, repo)
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.chdir(repo)
    with pytest.raises(Refusal, match="no clone for widgets"):
        Build(store, Rooted(repo)).locate("s1", "ACME")
    with pytest.raises(Refusal, match="no clone for widgets"):
        Build(store, Shell(), repo).locate("s1", "ACME")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(f'[clones]\nwidgets = "{repo}"\n', encoding="utf-8")
    monkeypatch.setenv("DECKHAND_CONFIG_DIR", str(config_dir))
    assert Build(store, Shell(), tmp_path / "elsewhere").locate("s1", "ACME").clone == repo


def nested(worktree: Path, tmp_path: Path, name: str = "n", tracked: bool = False) -> Path:
    marker = tmp_path / "marker"
    sh(worktree, "init", "-q", "--separate-git-dir", str(tmp_path / "store"), name)
    inner = worktree / name
    (inner / "f").write_text("a\n")
    sh(inner, "add", "f")
    sh(inner, "commit", "-qm", "chore: inner")
    sh(inner, "config", "filter.x.clean", f"touch {marker}; cat")
    (inner / ".gitattributes").write_text("* filter=x\n")
    if tracked:
        (worktree / ".gitmodules").write_text(f'[submodule "{name}"]\n\tpath = {name}\n\tignore = none\n')
        sh(worktree, "add", name, ".gitmodules")
        sh(worktree, "commit", "-qm", "chore: track n")
    marker.unlink(missing_ok=True)
    (inner / "f").write_text("b\n")
    return marker


def swapping(moved: Path, aside: Path, stand_in: bool) -> git.Runner:
    def hide() -> None:
        if not aside.exists():
            moved.rename(aside)
            if stand_in:
                moved.write_text("stand-in\n")

    def show() -> None:
        if aside.exists():
            if stand_in:
                moved.unlink()
            aside.rename(moved)

    def runner(argv: list[str], cwd: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        # The hand reshapes its worktree between git calls: harmless for every look, armed for git add.
        (show if argv[:2] == ["git", "add"] else hide)()
        return git.run(argv, cwd, stdin)

    return runner


def test_a_nested_repository_runs_nothing(tmp_path: Path, repo: Path, monkeypatch):
    store, worktree = real(tmp_path, repo, monkeypatch)
    marker = nested(worktree, tmp_path, tracked=True)
    (worktree / "a.txt").write_text("two\n")
    commit(store, repo, tmp_path, "feat: change a\n")
    (worktree / "a.txt").write_text("three\n")
    with pytest.raises(Refusal, match="uncommitted"):
        Build(store, git.run, repo).apply(["s1", "sync", *TEAM])
    Commit(cwd=worktree).context([])
    assert git.unmerged(worktree) == []
    assert not marker.exists()
    assert sh(worktree, "show", "--name-only", "--format=", "HEAD").splitlines() == ["a.txt"]


@pytest.mark.parametrize(
    ("name", "tracked", "moved", "stand_in"),
    [
        ("n", True, "n/.git", False),
        ("n", False, "n/.git", False),
        ("n", True, "n", True),
        ("d/n", True, "d", True),
    ],
    ids=["gitfile-hidden", "untracked-gitfile-hidden", "gitlink-as-file", "parent-as-file"],
)
def test_a_nested_repository_reshaped_between_git_calls_runs_nothing(
    tmp_path: Path, repo: Path, monkeypatch, name: str, tracked: bool, moved: str, stand_in: bool
):
    store, worktree = real(tmp_path, repo, monkeypatch)
    marker = nested(worktree, tmp_path, name, tracked)
    (worktree / "a.txt").write_text("two\n")
    commit(store, repo, tmp_path, "feat: change a\n", swapping(worktree / moved, tmp_path / "aside", stand_in))
    assert not marker.exists()
    assert sh(worktree, "show", "--name-only", "--format=", "HEAD").splitlines() == ["a.txt"]


def test_a_file_swapped_for_a_repository_is_never_staged(tmp_path: Path, repo: Path, monkeypatch):
    store, worktree = real(tmp_path, repo, monkeypatch)
    nested(worktree, tmp_path)
    (worktree / "a.txt").write_text("two\n")
    with pytest.raises(Refusal, match="a commit takes no nested repository: n"):
        commit(store, repo, tmp_path, "feat: change a\n", swapping(worktree / "n", tmp_path / "aside", True))
    assert "160000" not in sh(worktree, "ls-files", "-s")
    assert sh(worktree, "log", "-1", "--format=%s") == "chore: base"


def test_a_gitlink_staged_by_a_failing_add_is_never_committed(tmp_path: Path, repo: Path, monkeypatch):
    store, worktree = real(tmp_path, repo, monkeypatch)
    nested(worktree, tmp_path)
    (worktree / "q").write_text("q\n")
    swap = swapping(worktree / "n", tmp_path / "aside", True)

    def failing(argv: list[str], cwd: Path | None = None, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["git", "add"]:
            (worktree / ".gitignore").write_text("q\n")
        return swap(argv, cwd, stdin)

    with pytest.raises(Refusal, match="a commit takes no nested repository: n"):
        commit(store, repo, tmp_path, "feat: add q\n", failing)
    assert "160000" not in sh(worktree, "ls-files", "-s")
    (worktree / ".gitignore").unlink()
    (worktree / "n").unlink()
    (tmp_path / "aside").rename(worktree / "n")
    with pytest.raises(Refusal, match="a commit takes no nested repository: n"):
        commit(store, repo, tmp_path, "feat: add q\n")
    assert "160000" not in sh(worktree, "ls-tree", "-r", "HEAD")


def test_a_gitlink_an_interrupted_commit_left_is_removed_and_refused(tmp_path: Path, repo: Path, monkeypatch):
    store, worktree = real(tmp_path, repo, monkeypatch)
    nested(worktree, tmp_path)
    sh(worktree, "update-index", "--add", "--cacheinfo", f"160000,{sh(worktree / 'n', 'rev-parse', 'HEAD')},n")
    shutil.move(worktree / "n", tmp_path / "aside")
    (worktree / "a.txt").write_text("two\n")
    with pytest.raises(Refusal, match="a commit takes no nested repository: n"):
        commit(store, repo, tmp_path, "feat: change a\n")
    assert "160000" not in sh(worktree, "ls-files", "-s")
    assert sh(worktree, "log", "-1", "--format=%s") == "chore: base"
    commit(store, repo, tmp_path, "feat: change a\n")
    assert sh(worktree, "show", "--name-only", "--format=", "HEAD").splitlines() == ["a.txt"]


def test_a_submodule_the_clone_holds_still_commits(tmp_path: Path, repo: Path, monkeypatch):
    store, worktree = real(tmp_path, repo, monkeypatch)
    sh(worktree, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(tmp_path / "origin.git"), "sub")
    sh(worktree, "commit", "-qm", "chore: add sub")
    (worktree / "a.txt").write_text("two\n")
    commit(store, repo, tmp_path, "feat: change a\n")
    assert sh(worktree, "log", "-1", "--format=%s") == "feat: change a"
    assert sh(worktree, "show", "--name-only", "--format=", "HEAD").splitlines() == ["a.txt"]


def test_commit_refuses_a_gitlink(tmp_path: Path, repo: Path, monkeypatch):
    store, worktree = real(tmp_path, repo, monkeypatch)
    marker = nested(worktree, tmp_path)
    (worktree / "a.txt").write_text("two\n")
    with pytest.raises(Refusal, match="a commit takes no nested repository: n"):
        commit(store, repo, tmp_path, "feat: change a\n")
    assert not marker.exists()
    assert "160000" not in sh(worktree, "ls-files", "-s")
    assert sh(worktree, "rev-parse", "HEAD") == sh(worktree, "rev-parse", BRANCH)
    assert sh(worktree, "log", "-1", "--format=%s") == "chore: base"


def as_hand(tmp_path: Path, repo: Path, worktree: Path, monkeypatch) -> tuple[Path, Path]:
    config, scratch = tmp_path / "hand-config", tmp_path / "scratch"
    config.mkdir()
    scratch.mkdir()
    (config / "config.toml").write_text(f'[clones]\nwidgets = "{repo}"\n', encoding="utf-8")
    monkeypatch.setenv("DECKHAND_CONFIG_DIR", str(config))
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.chdir(worktree)
    return config, scratch


def hand_commit(store: FakeStore, repo: Path, path: Path) -> None:
    Build(store, git.run, repo).apply(["s1", "commit", "--message", str(path), *TEAM])


def test_a_hand_cannot_read_a_config_file_through_a_temporary_symlink(tmp_path: Path, repo: Path, monkeypatch):
    store, worktree = real(tmp_path, repo, monkeypatch)
    config, scratch = as_hand(tmp_path, repo, worktree, monkeypatch)
    (config / "secret").write_text("feat: lin_api_secretvalue\n")
    (scratch / "message").symlink_to(config / "secret")
    (worktree / "a.txt").write_text("two\n")
    with pytest.raises(Refusal, match="a hand reads files only from its worktree or the temporary directory"):
        hand_commit(store, repo, scratch / "message")
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(scratch / "state"))
    (scratch / "state").mkdir()
    (scratch / "state" / "message").write_text("feat: from the state dir\n")
    with pytest.raises(Refusal, match="a hand reads files only from its worktree or the temporary directory"):
        hand_commit(store, repo, scratch / "state" / "message")
    assert sh(worktree, "log", "-1", "--format=%s") == "chore: base"


def test_a_hand_reads_its_temporary_and_worktree_files(tmp_path: Path, repo: Path, monkeypatch):
    store, worktree = real(tmp_path, repo, monkeypatch)
    _, scratch = as_hand(tmp_path, repo, worktree, monkeypatch)
    (scratch / "message").write_text("feat: from the temporary directory\n")
    (worktree / "a.txt").write_text("two\n")
    hand_commit(store, repo, scratch / "message")
    (worktree / "message").write_text("feat: from the worktree\n")
    hand_commit(store, repo, worktree / "message")
    assert sh(worktree, "log", "-2", "--format=%s").splitlines() == [
        "feat: from the worktree",
        "feat: from the temporary directory",
    ]


def test_context_of_a_merged_story_names_its_after_merge_work(capsys: pytest.CaptureFixture[str]):
    from tests.test_step_decide import feature_store, released

    store = released(feature_store())
    capsys.readouterr()
    Build(store).context(["ACME-1", *TEAM])
    assert capsys.readouterr().out.splitlines() == [
        "ACME-1 has merged; do its after-merge items from deckhand after context ACME-1"
    ]
