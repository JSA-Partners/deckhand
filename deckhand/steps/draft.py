from __future__ import annotations

from deckhand import claims, features, inputs, ops, planning, queue
from deckhand.features import Phase
from deckhand.records import HAND, Record, content_hash
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import StoryStep
from deckhand.store import Store

USAGE = "draft takes REF and [--team-key KEY] on both verbs, and on apply --file DRAFT.md or take"
PURPOSE = "draft"
TITLE = "Draft"
SHAPE = (
    "Outcome: what is true once the story is done",
    "Examples: the cases the story must handle, one line each",
    "Out of scope: what the story leaves to another",
    "Unknowns settled: what the stub left open, and the answer",
)


def _parse(args: list[str]) -> dict:
    found: dict = {"words": []}
    rest = iter(args)
    for arg in rest:
        if arg in ("--team-key", "--file"):
            found[arg[2:].replace("-", "_")] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    words = found["words"]
    if not words or len(words) > 2 or (len(words) == 2 and words[1] != "take"):
        raise Refusal(f"draft takes a story REF, then take; {USAGE}")
    return found


@register("draft")
class Draft(StoryStep):
    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        store = self._store_for_run()
        feature = features.owning(store, team_key(parsed), parsed["words"][0])
        story = features.story(store, feature, parsed["words"][0])
        print(f"story: {story['identifier']} {story['title']}")
        print(story["description"] or "no description")
        stub = next((r for r in feature.records.get(story["id"], []) if r.kind == "stub"), None)
        for name in ("unknowns", "new_here", "decisions"):
            for item in stub.data.get(name, []) if stub else []:
                print(f"{name}: {item}")
        print("contract:")
        print(planning.section(store, feature, "Contract") or "none saved")
        print("the draft holds, in this order:")
        for part in SHAPE:
            print(f"- {part}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        ref, taking = parsed["words"][0], parsed["words"][1:] == ["take"]
        store = self._store_for_run()
        feature = features.owning(store, team_key(parsed), ref)
        story = features.story(store, feature, ref)
        phase = features.phase(feature)
        if phase is not Phase.PLANNING:
            raise Refusal(f"{features.describe(feature.project)} is {phase.value}, not planning")
        claims.owned(story, self.viewer())
        if taking:
            self._take(store, story)
        else:
            self._save(store, feature, story, parsed.get("file", ""))

    def _save(self, store: Store, feature: features.Feature, story: dict, path: str) -> None:
        text = inputs.read(path, "--file", USAGE)
        if not features.contract_approved(store, feature):
            raise Refusal("the Contract is not approved; a draft waits for it")
        if not text.strip():
            raise Refusal("the draft is empty")
        ops.ensure_document(store, story["id"], PURPOSE, story["id"], TITLE, text)
        record = Record(queue.DRAFT, story["id"], content_hash(text), "agent:draft", {})
        ops.ensure_record(store, story["id"], record)
        print(f"done: saved the draft for {story['identifier']}")

    def _take(self, store: Store, story: dict) -> None:
        held = store.document(ops.create_id(story["id"], PURPOSE))
        if held is None:
            raise Refusal(f"{story['identifier']} has no draft to take")
        ops.ensure_issue_text(store, story["id"], story["title"], held["body"])
        record = Record(queue.TAKEN, story["id"], content_hash(held["body"]), HAND, {})
        ops.ensure_record(store, story["id"], record)
        print(f"done: took the draft as the description of {story['identifier']}")
