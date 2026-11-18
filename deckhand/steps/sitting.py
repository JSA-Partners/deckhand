from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from deckhand import answers, features, ops, sitting, tiers, versions
from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.step import Refusal, register
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = (
    "sitting takes REF and --team-key KEY on both verbs; context [--contract NAME ...] [--json],"
    " apply --file ANSWERS.md"
)
TITLE = "Sitting"


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


def _parse(args: list[str]) -> dict:
    found: dict = {"json": False, "contracts": [], "words": []}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg in ("--team-key", "--file", "--contract"):
            value = next(rest, "")
            if arg == "--contract":
                found["contracts"].append(value)
            else:
                found[arg[2:].replace("-", "_")] = value
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    if not found.get("team_key"):
        raise Refusal(f"--team-key is required; {USAGE}")
    if len(found["words"]) != 1:
        raise Refusal(f"sitting takes one story REF; {USAGE}")
    return found


@register("sitting")
class Sitting:
    def __init__(self, store: Store | None = None, now: Callable[[], datetime] = datetime.now) -> None:
        self._store = store
        self._now = now

    def _store_for_run(self) -> Store:
        if self._store is not None:
            return self._store
        return LinearStore(connect(Credentials.load(_config_dir()), "app"))

    def _story(self, store: Store, parsed: dict) -> tuple[features.Feature, dict]:
        feature = features.open_feature(store, parsed["team_key"])
        if feature is None:
            raise Refusal("no feature is open")
        return feature, features.story(store, feature, parsed["words"][0])

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        store = self._store_for_run()
        feature, story = self._story(store, parsed)
        records = feature.records.get(story["id"], [])
        tier = versions.held(records, "tier", story["id"])
        if tier is None or not versions.current(store, feature, tier):
            raise Refusal(f"the tier of {story['identifier']} is not decided for its current text; decide it first")
        level = tiers.tier(tiers.chosen(str(tier.data.get("answer", ""))))
        stub = next((r.data for r in records if r.kind == "stub"), {})
        questions = sitting.questions_for(
            story, level, stub.get("unknowns", []), stub.get("new_here", []), parsed["contracts"]
        )
        now = self._now()
        line = sitting.progress(store, feature, sitting.started(answers.state_dir(), feature.project["id"], now), now)
        facts = {
            "story": story["identifier"],
            "title": story["title"],
            "tier": level.level,
            "questions": questions,
            "progress": line,
        }
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        print(f"story: {facts['story']} {facts['title']}")
        print(f"tier: {facts['tier']}")
        for number, question in enumerate(questions, 1):
            print(f"{number}. {question}")
        if not questions:
            print("no questions: nothing in this story carries risk")
        print(line)

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        if not parsed.get("file"):
            raise Refusal(f"--file is required; {USAGE}")
        store = self._store_for_run()
        _, story = self._story(store, parsed)
        try:
            body = Path(parsed["file"]).read_text().strip()
        except OSError:
            raise Refusal(f"{parsed['file']}: cannot be read") from None
        if not body:
            raise Refusal(f"{parsed['file']} is empty")
        ops.ensure_document(store, story["id"], "sitting", story["id"], TITLE, body)
        print(f"done: saved the sitting for {story['identifier']}")
