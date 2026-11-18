from __future__ import annotations

import json
import os
from pathlib import Path

from deckhand import features, planning
from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.step import Refusal, register
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = (
    "plan takes --team-key KEY on both verbs, --json on context only; context [SECTION],"
    " apply SECTION --file PATH [--drafted]"
)


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


def _parse(args: list[str]) -> dict:
    found: dict = {"json": False, "drafted": False, "words": []}
    rest = iter(args)
    for arg in rest:
        if arg in ("--json", "--drafted"):
            found[arg[2:]] = True
        elif arg in ("--team-key", "--file"):
            found[arg[2:].replace("-", "_")] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    if not found.get("team_key"):
        raise Refusal(f"--team-key is required; {USAGE}")
    return found


@register("plan")
class Plan:
    def __init__(self, store: Store | None = None) -> None:
        self._store = store

    def _store_for_run(self) -> Store:
        if self._store is not None:
            return self._store
        return LinearStore(connect(Credentials.load(_config_dir()), "app"))

    def _feature(self, store: Store, parsed: dict) -> features.Feature:
        feature = features.open_feature(store, parsed["team_key"])
        if feature is None:
            raise Refusal("no feature is open")
        return feature

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        store = self._store_for_run()
        feature = self._feature(store, parsed)
        if len(parsed["words"]) > 1:
            raise Refusal(f"context takes at most one SECTION; {USAGE}")
        if parsed["words"]:
            name = parsed["words"][0]
            text = planning.section(store, feature, name)
            if parsed["json"]:
                print(json.dumps({"section": name, "text": text}, indent=2))
            else:
                print(text if text is not None else f"the {name} section is empty")
            return
        saved = {name: planning.section(store, feature, name) is not None for name in planning.SECTIONS}
        following = planning.next_section(store, feature)
        if parsed["json"]:
            print(json.dumps({"sections": saved, "next": following}, indent=2))
            return
        for name, held in saved.items():
            print(f"{name}: {'saved' if held else 'empty'}")
        print(f"next: {following}" if following else "next: nothing")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        if len(parsed["words"]) != 1 or not parsed.get("file"):
            raise Refusal(f"apply takes SECTION and --file; {USAGE}")
        name = parsed["words"][0]
        try:
            body = Path(parsed["file"]).read_text()
        except OSError:
            raise Refusal(f"{parsed['file']}: cannot be read") from None
        store = self._store_for_run()
        planning.save_section(store, self._feature(store, parsed), name, body, parsed["drafted"])
        print(f"done: saved {name}")
