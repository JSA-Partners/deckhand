from __future__ import annotations

import json
import os
from pathlib import Path

from deckhand import features, ops, planning
from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.records import Record
from deckhand.step import Refusal, register
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = "stories takes --team-key KEY on both verbs, --json on context only, and --file MAP.json on apply"
MISSED_BY = ("rule", "example", "invariant", "spike", "external")
_LISTS = ("depends_on", "unknowns", "new_here")
_REQUIRED = ("key", "title", "outcome", "repository")
_OPTIONAL = ("missed_by", "cuts", "spends_budget", "decisions")


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


def _parse(args: list[str]) -> dict:
    found: dict = {"json": False}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg in ("--team-key", "--file"):
            found[arg[2:].replace("-", "_")] = next(rest, "")
        else:
            raise Refusal(f"unknown argument {arg}; {USAGE}")
    if not found.get("team_key"):
        raise Refusal(f"--team-key is required; {USAGE}")
    return found


def _read_map(path: str) -> list[dict]:
    if not path:
        raise Refusal(f"--file is required; {USAGE}")
    try:
        entries = json.loads(Path(path).read_text())
    except OSError:
        raise Refusal(f"{path}: cannot be read") from None
    except ValueError:
        raise Refusal(f"{path}: not valid JSON") from None
    if not isinstance(entries, list):
        raise Refusal("the story map must be a list of objects")
    for index, entry in enumerate(entries):
        _check_entry(index, entry)
    return entries


def _check_entry(index: int, entry: object) -> None:
    if not isinstance(entry, dict):
        raise Refusal(f"entry {index} is not an object")
    for name in entry:
        if name not in (*_REQUIRED, *_LISTS, *_OPTIONAL):
            raise Refusal(f"entry {index} has an unknown field {name}")
    for name in _REQUIRED:
        value = entry.get(name)
        if not isinstance(value, str) or not value.strip():
            raise Refusal(f"entry {index} field {name} must be a non-empty string")
    for name in _LISTS:
        value = entry.get(name)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise Refusal(f"entry {index} field {name} must be a list of strings")
    decisions = entry.get("decisions", [])
    if not isinstance(decisions, list) or not all(isinstance(item, str) for item in decisions):
        raise Refusal(f"entry {index} field decisions must be a list of strings")
    for name in ("missed_by", "cuts"):
        if name in entry and not isinstance(entry[name], str):
            raise Refusal(f"entry {index} field {name} must be a string")
    if "spends_budget" in entry and not isinstance(entry["spends_budget"], bool):
        raise Refusal(f"entry {index} field spends_budget must be true or false")


def _check_graph(entries: list[dict]) -> None:
    keys = [e["key"] for e in entries]
    for key in sorted({k for k in keys if keys.count(k) > 1}):
        raise Refusal(f"the key {key} is used more than once")
    after = {e["key"]: e["depends_on"] for e in entries}
    for key, deps in after.items():
        for dep in deps:
            if dep not in after:
                raise Refusal(f"{key} depends on {dep}, which is nowhere in the map")
    done: set[str] = set()

    def visit(key: str, path: tuple[str, ...]) -> None:
        if key in path:
            raise Refusal("the dependencies form a cycle: " + " -> ".join((*path[path.index(key) :], key)))
        if key in done:
            return
        for dep in after[key]:
            visit(dep, (*path, key))
        done.add(key)

    for key in after:
        visit(key, ())


def _description(entry: dict) -> str:
    parts = [f"Outcome: {entry['outcome'].strip()}"]
    for heading, name in (("Unknowns", "unknowns"), ("New here", "new_here")):
        if entry[name]:
            parts.append("\n".join([f"{heading}:", *[f"- {item}" for item in entry[name]]]))
    return "\n\n".join(parts)


def _story_id(feature: features.Feature, key: str) -> str:
    return ops.create_id(feature.project["id"], "story:" + key)


def _check_discovery(feature: features.Feature, entries: list[dict]) -> None:
    held = {s["id"] for s in feature.stories}
    keys = {e["key"] for e in entries}
    approved = any(feature.approved(s) for s in feature.live)
    for entry in entries:
        key = entry["key"]
        new = _story_id(feature, key) not in held
        missed = entry.get("missed_by")
        if missed is not None and missed not in MISSED_BY:
            raise Refusal(f"{key}: missed_by must be one of {', '.join(MISSED_BY)}")
        cut = entry.get("cuts")
        if cut is not None:
            if cut in keys:
                raise Refusal(f"{key} cuts {cut}, which is still in the map")
            if _story_id(feature, cut) not in held:
                raise Refusal(f"{key} cuts {cut}, which is not a story of this feature")
        if approved and new:
            if missed is None:
                raise Refusal(f"{key} is new after approval; it needs missed_by, one of {', '.join(MISSED_BY)}")
            if cut is None and entry.get("spends_budget") is not True:
                raise Refusal(f"{key} is new after approval; it needs cuts (a story key) or spends_budget true")


def _discovery(entry: dict) -> dict:
    found = {"missed_by": entry["missed_by"]}
    for name in ("cuts", "spends_budget"):
        if name in entry:
            found[name] = entry[name]
    return found


@register("stories")
class Stories:
    def __init__(self, store: Store | None = None) -> None:
        self._store = store

    def _store_for_run(self) -> Store:
        if self._store is not None:
            return self._store
        return LinearStore(connect(Credentials.load(_config_dir()), "app"))

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        feature = features.open_feature(self._store_for_run(), parsed["team_key"])
        if feature is None:
            raise Refusal("no feature is open")
        facts = {"feature": feature.project["name"], "stories": len(feature.stories), "next": planning.STORY_MAP}
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        print(f"feature: {facts['feature']}, {facts['stories']} stories")
        print("map: a JSON list of {key, title, outcome, repository, depends_on, unknowns, new_here}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        entries = _read_map(parsed.get("file", ""))
        _check_graph(entries)
        store = self._store_for_run()
        team = parsed["team_key"]
        feature = features.open_feature(store, team)
        if feature is None:
            raise Refusal("no feature is open")
        known = store.repository_labels(team)
        for entry in entries:
            if entry["repository"] not in known:
                raise Refusal(f"{entry['key']}: {entry['repository']} is not a repository label of team {team}")
        _check_discovery(feature, entries)
        project = feature.project["id"]
        ids = {e["key"]: _story_id(feature, e["key"]) for e in entries}
        for entry in entries:
            id = ops.ensure_issue(
                store,
                project,
                "story:" + entry["key"],
                team=team,
                title=entry["title"],
                description=_description(entry),
                project=project,
                labels=[entry["repository"]],
            )
            ops.ensure_issue_text(store, id, entry["title"], _description(entry))
            ops.ensure_labels(store, id, [entry["repository"]], known)
            for dep in entry["depends_on"]:
                ops.ensure_relation(store, ids[dep], id)
            stub = {
                "unknowns": entry["unknowns"],
                "new_here": entry["new_here"],
                "decisions": entry.get("decisions", []),
            }
            ops.ensure_record(store, id, Record("stub", entry["key"], "1", "agent:stories", stub))
            if "missed_by" in entry:
                record = Record("discovered", entry["key"], "1", "agent:stories", _discovery(entry))
                ops.ensure_record(store, id, record)
        print(f"done: {len(entries)} stories in the map for {feature.project['name']}")
