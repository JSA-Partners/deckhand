from __future__ import annotations

import os

from deckhand import claims, features, inputs, ops, queue, spec
from deckhand.features import Phase
from deckhand.records import Record, content_hash
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import StoryStep
from deckhand.store import Store

USAGE = "draft takes REF and [--team-key KEY] on both verbs, and on apply --file DRAFT.md or take"
PURPOSE = "draft"
TITLE = "Draft"
SHAPE = (
    "Outcome: what is true once the story is done",
    "Examples: the cases of the approved Examples this story owns, one line each",
    "Out of scope: what the story leaves to another",
    "Unknowns: what the stub left open, each listed as a question for the person, never settled here",
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
        ref = story["identifier"]
        stub = next((r for r in feature.records.get(story["id"], []) if r.kind == "stub"), None)

        def items(name: str) -> list[str]:
            return [f"{name}: {item}" for item in (stub.data.get(name, []) if stub else [])]

        first = [f"story: {ref} {story['title']}", spec.planning_text(store, feature, ("Contract",))]
        first += [*items("decisions"), spec.planning_text(store, feature, ("Frame", "Shape", "Examples"))]
        rest = [story["description"] or "no description", *items("unknowns"), *items("new_here")]
        rest += ["the draft holds, in this order:", *(f"- {part}" for part in SHAPE)]
        print(spec.fitted(f"{ref}-draft", "\n".join(first) + "\n", "\n".join(rest) + "\n"), end="")

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
        if any(r.kind == queue.TAKEN for r in feature.records.get(story["id"], [])):
            raise Refusal(f"{story['identifier']} took its draft already; a new draft would not reach its description")
        ops.ensure_document(store, story["id"], PURPOSE, story["id"], TITLE, text)
        record = Record(queue.DRAFT, story["id"], content_hash(text), "agent:draft", {})
        ops.ensure_record(store, story["id"], record)
        print(f"done: saved the draft for {story['identifier']}")

    def _take(self, store: Store, story: dict) -> None:
        held = store.document(ops.create_id(story["id"], PURPOSE))
        if held is None:
            raise Refusal(f"{story['identifier']} has no draft to take")
        ops.ensure_issue_text(store, story["id"], story["title"], held["body"])
        author = f"person:{os.environ.get('CLAUDE_CODE_SESSION_ID', '')}"
        record = Record(queue.TAKEN, story["id"], content_hash(held["body"]), author, {})
        ops.ensure_record(store, story["id"], record)
        print(f"done: took the draft as the description of {story['identifier']}")
