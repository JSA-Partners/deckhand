from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from deckhand import config, features, git, launch, ops, planning
from deckhand.features import Phase
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import DEFAULT_BASE, fetch_origin
from deckhand.store import Store
from deckhand.store.open import Stores

USAGE = (
    "feature takes [--team-key KEY] on both verbs, --json on context only, and on apply one of:"
    " new --name N --weeks 1|2 [--reference REF ...], pick --project ID --weeks 1|2, park --project ID, fetch"
)
PLANNING = "planning"


def _parse(args: list[str]) -> dict:
    found: dict = {"references": [], "json": False}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg in ("--team-key", "--name", "--weeks", "--project", "--reference"):
            value = next(rest, "")
            if arg == "--reference":
                found["references"].append(value)
            else:
                found[arg[2:].replace("-", "_")] = value
        elif "action" not in found and arg in ("new", "pick", "park", "fetch"):
            found["action"] = arg
        else:
            raise Refusal(f"unknown argument {arg}; {USAGE}")
    return found


def _description(references: list[str]) -> str:
    if not references:
        return ""
    return "\n".join(["Reference (not a decision):", *[f"- {r}" for r in references]])


@register("feature")
class Feature:
    def __init__(
        self, store: Store | None = None, today: Callable[[], date] = date.today, runner: git.Runner = git.run
    ) -> None:
        self._stores = Stores(person=store)
        self._today = today
        self._runner = runner

    def _store_for_run(self) -> Store:
        return self._stores("person")

    def _facts(self, store: Store, team: str) -> dict:
        today = self._today()
        found = features.open_features(store, team)
        parked = [{"id": p["id"], "name": p["name"]} for p in features.parked(store, team)]
        facts: dict = {"open": [], "parked": parked}
        for feature in found:
            budget = features.budget_of(feature.project, today)
            current = features.phase(feature)
            replanning = features.replanning(feature)
            facts["open"].append(
                {
                    "id": feature.project["id"],
                    "name": feature.project["name"],
                    "phase": current.value,
                    "stories": len(feature.stories),
                    "next_planning_step": planning.next_section(store, feature),
                    "gaps": features.approval_gaps(store, feature, captain=True) if replanning else [],
                    "approval_void": feature.reapproval_due,
                    "story_hint": None
                    if budget is None
                    else {
                        "stories": len(feature.stories),
                        "hint": planning.HINT_PER_WEEK * budget.weeks,
                        "over": len(feature.stories) > planning.HINT_PER_WEEK * budget.weeks,
                    },
                    "budget": None
                    if budget is None
                    else {
                        "days_used": budget.days_used,
                        "budget_days": budget.budget_days,
                        "planning_note": budget.planning_note and current is Phase.PLANNING,
                        "over": budget.over,
                    },
                }
            )
        if len(found) > 1:
            names = ", ".join(features.describe(f.project) for f in found)
            facts["anomaly"] = f"more than one feature is open: {names}"
        return facts

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        folder = _input_folder()
        if folder and not parsed["json"]:
            print(f"input folder: {folder}", flush=True)
        read = self._planning()
        if not parsed["json"]:
            for row in read:
                print(_read_line(row), flush=True)
        facts = self._facts(self._store_for_run(), team_key(parsed)) | {"input_folder": folder, "planning": read}
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        if not facts["open"]:
            print("no feature is open")
        if "anomaly" in facts:
            print(f"anomaly: {facts['anomaly']}")
        for held in facts["open"]:
            print(f"open feature: {held['name']} ({held['id']}), {held['phase']}, {held['stories']} stories")
            if held["next_planning_step"]:
                print(f"next planning step: {held['next_planning_step']}")
            if held["approval_void"]:
                print("approval: the feature changed after it was approved; approve it again")
            for gap in held["gaps"]:
                print(f"gap: {gap}")
            hint = held["story_hint"]
            if hint and hint["over"]:
                print(f"note: {hint['stories']} stories against a hint of {hint['hint']} for the budget")
            budget = held["budget"]
            if budget is None:
                print("budget: the dates are not one or two weeks apart")
                continue
            print(f"budget: {budget['days_used']} of {budget['budget_days']} days used")
            if budget["over"]:
                print("over budget")
            if budget["planning_note"]:
                print("planning has passed half of one week")
        for row in facts["parked"]:
            print(f"parked: {row['name']} ({row['id']})")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        action = parsed.get("action")
        if action == "new":
            self._new(parsed)
        elif action in ("pick", "park"):
            self._move(action, parsed)
        elif action == "fetch":
            self._refresh()
        else:
            raise Refusal(f"apply takes new, pick, park or fetch; {USAGE}")

    # Planning reads every clone at a fresh origin, never the person's own checkout.
    def _refresh(self) -> None:
        clones = config.local().clones
        if not clones:
            raise Refusal(f"no clone is configured in {config.config_dir() / config.FILE}; run setup")
        for clone in clones.values():
            fetch_origin(clone, self._runner)
            path = git.worktree_path(clone, PLANNING)
            print(f"read the code in: {path} at {git.refresh_detached(clone, path, DEFAULT_BASE, self._runner)}")

    def _planning(self) -> list[dict]:
        found = []
        for label, clone in config.local().clones.items():
            path = git.worktree_path(clone, PLANNING)
            found.append({"label": label, "path": str(path), "sha": _sha(clone, path, self._runner)})
        return found

    def _new(self, parsed: dict) -> None:
        name = parsed.get("name", "").strip()
        if not name:
            raise Refusal(f"--name is required; {USAGE}")
        _weeks(parsed)
        store, team = self._store_for_run(), team_key(parsed)
        key = features.key(team, name)
        _none_open(store, team, ops.create_id(key, features.PURPOSE))
        self._refresh()
        id = ops.ensure_project(
            store, key, features.PURPOSE, team, name, _description(parsed["references"]), [features.FEATURE_LABEL]
        )
        print(f"done: opened {name} ({id}), target {self._open(store, id, parsed)}")

    def _open(self, store: Store, id: str, parsed: dict) -> str:
        held = store.project(id)
        if held is not None and held["start_date"] and held["target_date"]:
            return held["target_date"]
        today = self._today()
        target = today + timedelta(days=7 * int(parsed["weeks"]))
        ops.ensure_project_dates(store, id, today.isoformat(), target.isoformat())
        return target.isoformat()

    def _move(self, action: str, parsed: dict) -> None:
        if action == "pick":
            _weeks(parsed)
        store = self._store_for_run()
        team = team_key(parsed)
        wanted = parsed.get("project", "")
        project = next((p for p in features.team_features(store, team) if p["id"] == wanted), None)
        if project is None:
            raise Refusal(f"{wanted!r} is not a feature of team {team}")
        feature = features.load(store, project)
        current = features.phase(feature)
        if action == "pick":
            if current is not Phase.PARKED:
                raise Refusal(f"{features.describe(project)} is {current.value}, not parked")
            _none_open(store, team, project["id"])
            self._refresh()
            print(f"done: picked {project['name']}, target {self._open(store, project['id'], parsed)}")
            return
        if any(feature.approved(s) for s in feature.stories):
            raise Refusal(f"{features.describe(project)} has a story with a feature-approval record")
        if current is not Phase.PLANNING:
            raise Refusal(f"{features.describe(project)} is {current.value}, only a feature in planning can be parked")
        ops.ensure_project_dates(store, project["id"], None, None)
        print(f"done: parked {project['name']}")


def _weeks(parsed: dict) -> None:
    if parsed.get("weeks") not in ("1", "2"):
        raise Refusal(f"--weeks must be 1 or 2, not {parsed.get('weeks', '')!r}")


def _none_open(store: Store, team: str, id: str) -> None:
    held = [f for f in features.open_features(store, team) if f.project["id"] != id]
    if held:
        names = ", ".join(features.describe(f.project) for f in held)
        raise Refusal(f"{names} is still open; finish or park it first")


def _sha(clone: Path, path: Path, runner: git.Runner) -> str | None:
    if not path.is_dir():
        return None
    try:
        return git.head(path, runner) if git.is_worktree(clone, path, runner) else None
    except git.GitError:
        return None


def _read_line(row: dict) -> str:
    if row["sha"] is None:
        return f"read the code in: no planning worktree for {row['label']} yet; fetch one"
    return f"read the code in: {row['path']} at {row['sha']}"


def _input_folder() -> str | None:
    try:
        return str(launch.input_folder())
    except OSError:
        return None
