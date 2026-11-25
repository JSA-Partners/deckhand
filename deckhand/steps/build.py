from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from deckhand import claims, config, features, gh, git, implplan, inputs, ops, order, review, spec, tiers, versions
from deckhand.features import Phase
from deckhand.policy import Policy
from deckhand.records import Record
from deckhand.step import Refusal, hand_role, register, team_key
from deckhand.store import Store
from deckhand.store.open import Stores

USAGE = (
    "build takes REF and [--team-key KEY] on both verbs; context [--json], apply worktree, commit --message FILE,"
    " sync, or push after the plan review stopped clean"
)
ACTIONS = ("worktree", "commit", "sync", "push")
SUBJECT = re.compile(r"[a-z]+(\([^()\s]+\))?!?: \S.*")
TRAILER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*: ")
MESSAGE_RULE = (
    "the commit message takes a conventional subject under 72 characters, a blank line before any body, and no trailer"
)
WORKTREE = "worktree"
DEFAULT_BASE = "origin/HEAD"


def parse(args: list[str], usage: str) -> dict:
    found: dict = {"words": [], "json": False}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg in ("--team-key", "--message"):
            found[arg[2:].replace("-", "_")] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {usage}")
        else:
            found["words"].append(arg)
    return found


def message_of(path: str) -> str:
    text = inputs.read(path, "--message", USAGE)
    lines = text.strip().splitlines()
    subject = lines[0] if lines else ""
    last = "\n".join(lines[1:]).split("\n\n")[-1].splitlines()
    if (
        not SUBJECT.fullmatch(subject)
        or len(subject) >= 72
        or lines[1:2] not in ([], [""])
        or any(TRAILER.match(line) for line in last)
    ):
        raise Refusal(MESSAGE_RULE)
    return text.strip() + "\n"


def conflicted(worktree: Path, name: str) -> bool:
    path = worktree / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(worktree.resolve()):
        return False
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return any(line.startswith(("<<<<<<< ", ">>>>>>> ")) for line in lines)


def branch_of(records: list[Record], story: dict) -> str | None:
    held = versions.held(records, WORKTREE, story["id"])
    return held.data.get("branch") if held else None


@dataclass
class Located:
    feature: features.Feature
    story: dict
    records: list[Record]
    clone: Path
    policy: Policy
    path: Path | None
    head: str | None


class StoryStep:
    role = "app"

    def __init__(
        self,
        store: Store | None = None,
        runner: git.Runner = git.run,
        repo: Path | None = None,
        *,
        me: Callable[[], str] | None = None,
    ) -> None:
        self._stores = Stores(app=store, person=store)
        self._runner = runner
        self._repo = repo
        self._me = me

    def _store_for_run(self) -> Store:
        return self._stores(self.role)

    def viewer(self) -> str:
        if self._me is not None:
            return self._me()
        return claims.me(self._stores("person"))

    def owned(self, at: Located) -> None:
        claims.owned(at.story, self.viewer())

    def _clone(self, labels: list[str]) -> Path:
        clones = config.local().clones
        configured = next((clones[label] for label in labels if label in clones), None)
        if hand_role():
            if configured is None:
                where = config.config_dir() / config.FILE
                raise Refusal(f"no clone for {', '.join(labels)} in {where}; a hand takes its clone only from there")
            return configured
        return configured or self._repo or git.clone_root(Path.cwd(), self._runner)

    def _story(self, ref: str, team_key: str) -> tuple[features.Feature, dict, Policy, Path]:
        store = self._store_for_run()
        feature = features.open_feature(store, team_key)
        if feature is None:
            raise Refusal("no feature is open")
        current = features.phase(feature)
        if current is not Phase.BUILDING:
            raise Refusal(f"{features.describe(feature.project)} is {current.value}, not building; approve it first")
        story = features.story(store, feature, ref)
        clone = self._clone(story["labels"])
        policy = Policy.load(clone)
        if policy.repository_label not in story["labels"]:
            raise Refusal(
                f"{story['identifier']} belongs to another repository; this clone is {policy.repository_label}"
            )
        return feature, story, policy, clone

    def merged(self, ref: str, team_key: str) -> str | None:
        store = self._store_for_run()
        story = features.story(store, features.owning(store, team_key, ref), ref)
        if story["state"] != order.DONE:
            return None
        ref = story["identifier"]
        return f"{ref} has merged; do its after-merge items from deckhand after context {ref}"

    def locate(self, ref: str, team_key: str) -> Located:
        feature, story, policy, clone = self._story(ref, team_key)
        records = feature.records.get(story["id"], [])
        branch = branch_of(records, story)
        path = git.worktree_path(clone, branch) if branch else None
        if path is None or not path.is_dir():
            return Located(feature, story, records, clone, policy, None, None)
        return Located(feature, story, records, clone, policy, path, git.head(path, self._runner))


@register("build")
class Build(StoryStep):
    def context(self, args: list[str]) -> None:
        parsed = parse(args, USAGE)
        if len(parsed["words"]) != 1:
            raise Refusal(f"build takes one story REF; {USAGE}")
        merged = self.merged(parsed["words"][0], team_key(parsed))
        if merged is not None:
            print(merged)
            return
        store = self._store_for_run()
        at = self.locate(parsed["words"][0], team_key(parsed))
        story, policy, clone, records = at.story, at.policy, at.clone, at.records
        held = versions.held(records, "tier", story["id"])
        level = tiers.from_record(held.data) if held else None
        plan = store.document(ops.create_id(story["id"], implplan.PURPOSE))
        sitting = store.document(ops.create_id(story["id"], "sitting"))
        branch = branch_of(records, story) or story["branch_name"]
        approved = spec.gather(store, at.feature, story)
        facts = {
            "story": story["identifier"],
            "title": story["title"],
            "description": story["description"] or "",
            "tier": level.level if level else None,
            "tools": tiers.tools(policy.tier_tools, level.level, records, story["id"]) if level else [],
            "checks": policy.checks,
            "worktree": str(git.worktree_path(clone, branch)),
            "branch": branch,
            "contract": approved.contract,
            "examples": approved.examples,
            "invariants": approved.invariants,
            "decisions": approved.decisions,
            "plan": plan["body"] if plan else None,
            "sitting": sitting["body"] if sitting else None,
        }
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        _print(facts, spec.text(approved))

    def apply(self, args: list[str]) -> None:
        parsed = parse(args, USAGE)
        if len(parsed["words"]) != 2 or parsed["words"][1] not in ACTIONS:
            raise Refusal(f"apply takes REF and worktree, commit, sync or push; {USAGE}")
        ref, action = parsed["words"]
        message = message_of(parsed.get("message", "")) if action == "commit" else None
        at = self.locate(ref, team_key(parsed))
        self.owned(at)
        feature, story, clone = at.feature, at.story, at.clone
        require_base(clone, self._runner)
        if action == "worktree":
            self._worktree(feature, story, clone)
        elif message is not None:
            self._commit(at, message)
        elif action == "sync":
            sync(_worktree_of(at), story["identifier"], self._runner)
        else:
            self._push(feature, story, clone)

    def _commit(self, at: Located, message: str) -> None:
        path = _worktree_of(at)
        if not git.merging(path, self._runner) and not git.dirty(path, self._runner):
            raise Refusal(f"nothing to commit in {path}")
        marked = [name for name in git.unmerged(path, self._runner) if conflicted(path, name)]
        if marked:
            raise Refusal(f"conflict markers remain in {', '.join(marked)}; resolve them, then commit again")
        git.commit_all(path, message, self._runner)
        print(f"done: committed {git.head(path, self._runner)} on {at.story['identifier']}")

    def _worktree(self, feature: features.Feature, story: dict, clone: Path) -> None:
        branch = worktree_branch(story, feature.records.get(story["id"], []))
        path = make_worktree(self._store_for_run(), story, clone, branch, self._runner)
        print(f"done: worktree for {story['identifier']} at {path}")

    def _push(self, feature: features.Feature, story: dict, clone: Path) -> None:
        records = feature.records.get(story["id"], [])
        plan = versions.held(records, implplan.PURPOSE, story["id"])
        if plan is None or not review.cleared(records, story["id"], "plan", plan.version):
            raise Refusal(
                f"the plan review of {story['identifier']} has not stopped clean at the current plan;"
                " run a plan round; at the cap the person decides"
            )
        undecided = review.delivery_gaps(records, story["id"], "plan", story["identifier"])
        if undecided:
            raise Refusal("; ".join(undecided))
        branch = branch_of(records, story)
        path = git.worktree_path(clone, branch) if branch else None
        if branch is None or path is None or not path.is_dir():
            raise Refusal(f"{story['identifier']} has no worktree; run build apply {story['identifier']} worktree")
        if git.merge_base(path, "HEAD", DEFAULT_BASE, self._runner) == git.head(path, self._runner):
            raise Refusal(f"{branch} has no commits beyond {DEFAULT_BASE}; commit the work first")
        repo = gh.repo_name(clone, self._runner)
        git.push(path, branch, self._runner)
        title = f"{story['identifier']} {story['title']}"
        found = gh.ensure_draft_pr(branch, repo, title, f"Draft for {story['identifier']}.", self._runner)
        print(f"done: pushed {branch} and opened the draft pull request {found['url']}")


def sync(path: Path, ref: str, runner: git.Runner, leave: str | None = None) -> None:
    if git.dirty(path, runner):
        raise Refusal(
            f"{path} has uncommitted changes; {leave or f'run build apply {ref} commit --message FILE first'}"
        )
    conflicts = git.merge_from(path, DEFAULT_BASE, runner)
    if conflicts and leave:
        git.abort_merge(path, runner)
    if conflicts:
        fix = leave or f"resolve the markers, then run build apply {ref} commit --message FILE"
        raise Refusal(f"merging {DEFAULT_BASE} into {ref} conflicts in {', '.join(conflicts)}; {fix}")
    print(f"done: merged {DEFAULT_BASE} into {ref} at {git.head(path, runner)}")


def require_base(clone: Path, runner: git.Runner) -> None:
    if runner(["git", "rev-parse", "--verify", "--quiet", DEFAULT_BASE], clone, None).returncode != 0:
        raise Refusal("origin/HEAD is not set in this clone; run git remote set-head origin --auto")


def fetch_origin(clone: Path, runner: git.Runner) -> None:
    try:
        git.fetch(clone, runner)
    except git.GitError as error:
        raise Refusal(f"cannot fetch origin: {error.stderr_tail}") from None
    require_base(clone, runner)


def worktree_branch(story: dict, records: list[Record]) -> str:
    branch = branch_of(records, story) or story["branch_name"]
    if not branch:
        raise Refusal(f"{story['identifier']} has no branch name in Linear")
    return branch


def make_worktree(store: Store, story: dict, clone: Path, branch: str, runner: git.Runner) -> Path:
    fetch_origin(clone, runner)
    path = git.worktree_add(clone, branch, DEFAULT_BASE, runner)
    ops.ensure_record(store, story["id"], Record(WORKTREE, story["id"], branch, "agent:build", {"branch": branch}))
    return path


def _worktree_of(at: Located) -> Path:
    if at.path is None:
        ref = at.story["identifier"]
        raise Refusal(f"{ref} has no worktree; run build apply {ref} worktree")
    return at.path


def _print(facts: dict, approved: str) -> None:
    first = [f"story: {facts['story']} {facts['title']}", f"worktree: {facts['worktree']} (branch {facts['branch']})"]
    rest = [f"tier: {facts['tier'] if facts['tier'] is not None else 'not decided'}", "tools:"]
    rest += [f"- {tool}" for tool in facts["tools"]]
    rest += ["checks:", *(f"- {check}" for check in facts["checks"])]
    for name in ("plan", "sitting"):
        rest += [f"{name}:", facts[name] or "none saved"]
    head, tail = "\n".join(first), "\n".join(rest)
    print(spec.fitted(f"{facts['story']}-build", f"{head}\n{approved}\n", f"{tail}\n"), end="")
