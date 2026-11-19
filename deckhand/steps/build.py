from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from deckhand import features, gh, git, implplan, ops, review, tiers, versions
from deckhand.credentials import Credentials
from deckhand.features import Phase
from deckhand.linear.connect import connect
from deckhand.policy import Policy
from deckhand.records import Record
from deckhand.step import Refusal, register
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = (
    "build takes REF and --team-key KEY on both verbs; context [--json], apply worktree or apply push"
    " after the plan review stopped clean"
)
ACTIONS = ("worktree", "push")
WORKTREE = "worktree"
DEFAULT_BASE = "origin/HEAD"


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


def parse(args: list[str], usage: str) -> dict:
    found: dict = {"words": [], "json": False}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg == "--team-key":
            found["team_key"] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {usage}")
        else:
            found["words"].append(arg)
    if not found.get("team_key"):
        raise Refusal(f"--team-key is required; {usage}")
    return found


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
    def __init__(self, store: Store | None = None, runner: git.Runner = git.run, repo: Path | None = None) -> None:
        self._store = store
        self._runner = runner
        self._repo = repo

    def _store_for_run(self) -> Store:
        if self._store is not None:
            return self._store
        return LinearStore(connect(Credentials.load(_config_dir()), "app"))

    def _clone(self) -> Path:
        return self._repo or git.clone_root(Path.cwd(), self._runner)

    def _story(self, ref: str, team_key: str, clone: Path) -> tuple[features.Feature, dict, Policy]:
        store = self._store_for_run()
        feature = features.open_feature(store, team_key)
        if feature is None:
            raise Refusal("no feature is open")
        current = features.phase(feature)
        if current is not Phase.BUILDING:
            raise Refusal(f"{features.describe(feature.project)} is {current.value}, not building; approve it first")
        story = features.story(store, feature, ref)
        policy = Policy.load(clone)
        if policy.repository_label not in story["labels"]:
            raise Refusal(
                f"{story['identifier']} belongs to another repository; this clone is {policy.repository_label}"
            )
        return feature, story, policy

    def locate(self, ref: str, team_key: str) -> Located:
        clone = self._clone()
        feature, story, policy = self._story(ref, team_key, clone)
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
        store = self._store_for_run()
        at = self.locate(parsed["words"][0], parsed["team_key"])
        story, policy, clone, records = at.story, at.policy, at.clone, at.records
        held = versions.held(records, "tier", story["id"])
        level = tiers.from_record(held.data) if held else None
        plan = store.document(ops.create_id(story["id"], implplan.PURPOSE))
        sitting = store.document(ops.create_id(story["id"], "sitting"))
        branch = branch_of(records, story) or story["branch_name"]
        facts = {
            "story": story["identifier"],
            "title": story["title"],
            "description": story["description"] or "",
            "tier": level.level if level else None,
            "tools": tiers.tools(policy.tier_tools, level.level, records, story["id"]) if level else [],
            "checks": policy.checks,
            "worktree": str(git.worktree_path(clone, branch)),
            "branch": branch,
            "plan": plan["body"] if plan else None,
            "sitting": sitting["body"] if sitting else None,
        }
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        _print(facts)

    def apply(self, args: list[str]) -> None:
        parsed = parse(args, USAGE)
        if len(parsed["words"]) != 2 or parsed["words"][1] not in ACTIONS:
            raise Refusal(f"apply takes REF and worktree or push; {USAGE}")
        ref, action = parsed["words"]
        at = self.locate(ref, parsed["team_key"])
        feature, story, clone = at.feature, at.story, at.clone
        if self._runner(["git", "rev-parse", "--verify", "--quiet", DEFAULT_BASE], clone, None).returncode != 0:
            raise Refusal("origin/HEAD is not set in this clone; run git remote set-head origin --auto")
        if action == "worktree":
            self._worktree(feature, story, clone)
            return
        self._push(feature, story, clone)

    def _worktree(self, feature: features.Feature, story: dict, clone: Path) -> None:
        records = feature.records.get(story["id"], [])
        branch = branch_of(records, story) or story["branch_name"]
        if not branch:
            raise Refusal(f"{story['identifier']} has no branch name in Linear")
        path = git.worktree_add(clone, branch, DEFAULT_BASE, self._runner)
        record = Record(WORKTREE, story["id"], branch, "agent:build", {"branch": branch})
        ops.ensure_record(self._store_for_run(), story["id"], record)
        print(f"done: worktree for {story['identifier']} at {path}")

    def _push(self, feature: features.Feature, story: dict, clone: Path) -> None:
        records = feature.records.get(story["id"], [])
        plan = versions.held(records, implplan.PURPOSE, story["id"])
        if plan is None or not review.cleared(records, story["id"], "plan", plan.version):
            raise Refusal(
                f"the plan review of {story['identifier']} has not stopped clean; finish it,"
                " or at the cap let the person decide: decide context review-cap"
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


def _print(facts: dict) -> None:
    print(f"story: {facts['story']} {facts['title']}")
    if facts["description"]:
        print(facts["description"])
    print(f"worktree: {facts['worktree']} (branch {facts['branch']})")
    print(f"tier: {facts['tier'] if facts['tier'] is not None else 'not decided'}")
    print("tools:")
    for tool in facts["tools"]:
        print(f"- {tool}")
    print("checks:")
    for check in facts["checks"]:
        print(f"- {check}")
    for name in ("plan", "sitting"):
        print(f"{name}:")
        print(facts[name] if facts[name] else "none saved")
