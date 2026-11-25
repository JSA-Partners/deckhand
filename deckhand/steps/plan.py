from __future__ import annotations

import json

from deckhand import features, inputs, planning, spec, spikes
from deckhand.step import Refusal, register, team_key
from deckhand.store import Store
from deckhand.store.open import Stores

USAGE = (
    "plan takes [--team-key KEY] on both verbs, --json on context only; context [SECTION],"
    " spike KEY, apply SECTION --file PATH [--drafted]"
)


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
    return found


@register("plan")
class Plan:
    def __init__(self, store: Store | None = None) -> None:
        self._stores = Stores(app=store)

    def _store_for_run(self) -> Store:
        return self._stores("app")

    def _feature(self, store: Store, parsed: dict) -> features.Feature:
        feature = features.open_feature(store, team_key(parsed))
        if feature is None:
            raise Refusal("no feature is open")
        return feature

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        store = self._store_for_run()
        feature = self._feature(store, parsed)
        words = parsed["words"]
        project = feature.project["id"]
        held = {k: d for d in spikes.answered(store, feature) if (k := spikes.key_of(project, d))}
        if words[:1] == ["spike"]:
            if len(words) != 2:
                raise Refusal(f"context spike takes one KEY; {USAGE}")
            found = held.get(words[1])
            print(spec.plain(found["body"]) if found else f"no answered spike {words[1]}")
            return
        if len(words) > 1:
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
        titles = {key: d["title"] for key, d in held.items()}
        if parsed["json"]:
            print(json.dumps({"sections": saved, "next": following, "spikes": titles}, indent=2))
            return
        for name, held in saved.items():
            print(f"{name}: {'saved' if held else 'empty'}")
        print(f"next: {following}" if following else "next: nothing")
        if titles:
            print("answered spikes:")
            for key, title in sorted(titles.items()):
                print(f"{key} {title}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        if len(parsed["words"]) != 1 or not parsed.get("file"):
            raise Refusal(f"apply takes SECTION and --file; {USAGE}")
        name = parsed["words"][0]
        body = inputs.read(parsed["file"], "--file", USAGE)
        store = self._store_for_run()
        planning.save_section(store, self._feature(store, parsed), name, body, parsed["drafted"])
        print(f"done: saved {name}")
