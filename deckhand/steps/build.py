from __future__ import annotations

import json
import os
from pathlib import Path

from deckhand import features, gh, git, implplan, ops, review, tiers, versions
from deckhand.credentials import Credentials
from deckhand.features import Phase
from deckhand.linear.connect import connect
from deckhand.policy import Policy
from deckhand.step import Refusal, register
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = (
    "build takes REF and --team-key KEY on both verbs; context [--json], apply worktree or apply push"
    " after the plan review stopped clean"
)
ACTIONS = ("worktree", "push")
DEFAULT_BASE = "origin/HEAD"


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


def _parse(args: list[str]) -> dict:
    found: dict = {"words": [], "json": False}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg == "--team-key":
            found["team_key"] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    if not found.get("team_key"):
        raise Refusal(f"--team-key is required; {USAGE}")
    return found


@register("build")
class Build:
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

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        if len(parsed["words"]) != 1:
            raise Refusal(f"build takes one story REF; {USAGE}")
        clone = self._clone()
        store = self._store_for_run()
        feature, story, policy = self._story(parsed["words"][0], parsed["team_key"], clone)
        records = feature.records.get(story["id"], [])
        held = versions.held(records, "tier", story["id"])
        level = tiers.from_record(held.data) if held else None
        plan = store.document(ops.create_id(story["id"], implplan.PURPOSE))
        sitting = store.document(ops.create_id(story["id"], "sitting"))
        facts = {
            "story": story["identifier"],
            "title": story["title"],
            "description": story["description"] or "",
            "tier": level.level if level else None,
            "tools": tiers.tools(policy.tier_tools, level.level, records, story["id"]) if level else [],
            "checks": policy.checks,
            "worktree": str(clone / ".worktrees" / story["branch_name"]),
            "branch": story["branch_name"],
            "plan": plan["body"] if plan else None,
            "sitting": sitting["body"] if sitting else None,
        }
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        _print(facts)

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        if len(parsed["words"]) != 2 or parsed["words"][1] not in ACTIONS:
            raise Refusal(f"apply takes REF and worktree or push; {USAGE}")
        ref, action = parsed["words"]
        clone = self._clone()
        feature, story, _ = self._story(ref, parsed["team_key"], clone)
        if not story.get("branch_name"):
            raise Refusal(f"{story['identifier']} has no branch name in Linear")
        if self._runner(["git", "rev-parse", "--verify", "--quiet", DEFAULT_BASE], clone, None).returncode != 0:
            raise Refusal("origin/HEAD is not set in this clone; run git remote set-head origin --auto")
        if action == "worktree":
            path = git.worktree_add(clone, story["branch_name"], DEFAULT_BASE, self._runner)
            print(f"done: worktree for {story['identifier']} at {path}")
            return
        self._push(feature, story, clone)

    def _push(self, feature: features.Feature, story: dict, clone: Path) -> None:
        records = feature.records.get(story["id"], [])
        if not review.plan_cleared(records, story["id"]):
            raise Refusal(
                f"the plan review of {story['identifier']} has not stopped clean; finish it,"
                " or at the cap let the person decide: decide context review-cap"
            )
        branch = story["branch_name"]
        path = clone / ".worktrees" / branch
        if not path.is_dir():
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
