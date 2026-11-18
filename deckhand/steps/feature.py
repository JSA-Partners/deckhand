from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from deckhand import features, ops, planning
from deckhand.credentials import Credentials
from deckhand.features import Phase
from deckhand.linear.connect import connect
from deckhand.step import Refusal, register
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = (
    "feature takes --team-key KEY on both verbs, --json on context only, and on apply one of: new --name N"
    " [--reference REF ...], pick --project ID --weeks 1|2, park --project ID"
)


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


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
        elif "action" not in found and arg in ("new", "pick", "park"):
            found["action"] = arg
        else:
            raise Refusal(f"unknown argument {arg}; {USAGE}")
    if not found.get("team_key"):
        raise Refusal(f"--team-key is required; {USAGE}")
    return found


def _description(references: list[str]) -> str:
    if not references:
        return ""
    return "\n".join(["Reference (not a decision):", *[f"- {r}" for r in references]])


@register("feature")
class Feature:
    def __init__(self, store: Store | None = None, today: Callable[[], date] = date.today) -> None:
        self._store = store
        self._today = today

    def _store_for_run(self) -> Store:
        if self._store is not None:
            return self._store
        return LinearStore(connect(Credentials.load(_config_dir()), "app"))

    def _facts(self, store: Store, team: str) -> dict:
        today = self._today()
        found = features.open_features(store, team)
        parked = [{"id": p["id"], "name": p["name"]} for p in features.parked(store, team)]
        facts: dict = {"open": [], "parked": parked}
        for feature in found:
            budget = features.budget_of(feature.project, today)
            current = features.phase(feature)
            facts["open"].append(
                {
                    "id": feature.project["id"],
                    "name": feature.project["name"],
                    "phase": current.value,
                    "stories": len(feature.stories),
                    "next_planning_step": planning.next_section(store, feature),
                    "gaps": features.approval_gaps(store, feature) if current is Phase.PLANNING else [],
                    "approval_void": current is Phase.PLANNING and any(feature.approved(s) for s in feature.live),
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
        facts = self._facts(self._store_for_run(), parsed["team_key"])
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
        else:
            raise Refusal(f"apply takes new, pick or park; {USAGE}")

    def _new(self, parsed: dict) -> None:
        name = parsed.get("name", "").strip()
        if not name:
            raise Refusal(f"--name is required; {USAGE}")
        team = parsed["team_key"]
        id = ops.ensure_project(
            self._store_for_run(),
            f"{team}:{name}",
            "feature",
            team,
            name,
            _description(parsed["references"]),
        )
        print(f"done: parked feature {name} ({id})")

    def _move(self, action: str, parsed: dict) -> None:
        if action == "pick" and parsed.get("weeks") not in ("1", "2"):
            raise Refusal(f"--weeks must be 1 or 2, not {parsed.get('weeks', '')!r}")
        store = self._store_for_run()
        project = store.project(parsed.get("project", ""))
        if project is None:
            raise Refusal(f"no feature has the id {parsed.get('project', '')!r}")
        feature = features.load(store, project)
        current = features.phase(feature)
        if action == "pick":
            if current is not Phase.PARKED:
                raise Refusal(f"{features.describe(project)} is {current.value}, not parked")
            held = features.open_features(store, parsed["team_key"])
            if held:
                names = ", ".join(features.describe(f.project) for f in held)
                raise Refusal(f"{names} is still open; finish or park it first")
            today = self._today()
            target = today + timedelta(days=7 * int(parsed["weeks"]))
            ops.ensure_project_dates(store, project["id"], today.isoformat(), target.isoformat())
            print(f"done: picked {project['name']}, target {target.isoformat()}")
            return
        if any(feature.approved(s) for s in feature.stories):
            raise Refusal(f"{features.describe(project)} has a story with a feature-approval record")
        if current is not Phase.PLANNING:
            raise Refusal(f"{features.describe(project)} is {current.value}, only a feature in planning can be parked")
        ops.ensure_project_dates(store, project["id"], None, None)
        print(f"done: parked {project['name']}")
