from __future__ import annotations

from deckhand import approval, claims, features, inputs, ops, planning, queue, spec, versions
from deckhand.features import Phase
from deckhand.records import Record, content_hash
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import StoryStep
from deckhand.store import Store

USAGE = "draft takes REF and [--team-key KEY] on both verbs, and on apply --file DRAFT.md, take or drop"
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
    if not words or len(words) > 2 or (len(words) == 2 and words[1] not in ("take", "drop")):
        raise Refusal(f"draft takes a story REF, then take or drop; {USAGE}")
    return found


@register("draft")
class Draft(StoryStep):
    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        store = self._store_for_run()
        feature = features.owning(store, team_key(parsed), parsed["words"][0])
        story = features.story(store, feature, parsed["words"][0])
        ref = story["identifier"]
        records = feature.records.get(story["id"], [])
        stub = next((r for r in records if r.kind == "stub"), None)

        def items(name: str) -> list[str]:
            return [f"{name}: {item}" for item in (stub.data.get(name, []) if stub else [])]

        def decisions() -> list[str]:
            items = versions.drafts(records).items()
            return [f"decisions: {item}: {approval.item_answer(records, item, draft)}" for item, draft in items]

        first = [f"story: {ref} {story['title']}", spec.planning_text(store, feature, ("Contract",))]
        first += [*decisions(), spec.planning_text(store, feature, ("Frame", "Shape", "Examples"))]
        rest = [story["description"] or "no description", *items("unknowns"), *items("new_here")]
        rest += ["the draft holds, in this order:", *(f"- {part}" for part in SHAPE)]
        print(spec.fitted(f"{ref}-draft", "\n".join(first) + "\n", "\n".join(rest) + "\n"), end="")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        ref, verb = parsed["words"][0], parsed["words"][1:]
        store = self._store_for_run()
        feature = features.owning(store, team_key(parsed), ref)
        story = features.story(store, feature, ref)
        phase = features.phase(feature)
        if phase is not Phase.PLANNING:
            raise Refusal(f"{features.describe(feature.project)} is {phase.value}, not planning")
        claims.owned(story, self.viewer())
        if verb == ["take"]:
            self._take(store, feature, story)
        elif verb == ["drop"]:
            self._drop(store, feature, story)
        else:
            self._save(store, feature, story, parsed.get("file", ""))

    def _save(self, store: Store, feature: features.Feature, story: dict, path: str) -> None:
        text = inputs.read(path, "--file", USAGE)
        if planning.section(store, feature, "Contract") is None:
            raise Refusal("the Contract is not saved; a draft waits for it")
        if not text.strip():
            raise Refusal("the draft is empty")
        if any(r.kind in queue.TAKEN_KINDS for r in feature.records.get(story["id"], [])):
            ref = story["identifier"]
            raise Refusal(f"{ref} took or dropped its draft already; a new draft would not reach its description")
        ops.ensure_document(store, story["id"], PURPOSE, story["id"], TITLE, text)
        record = Record(queue.DRAFT, story["id"], content_hash(text), "agent:draft", {})
        ops.ensure_record(store, story["id"], record)
        print(f"done: saved the draft for {story['identifier']}")

    def _take(self, store: Store, feature: features.Feature, story: dict) -> None:
        held = store.document(ops.create_id(story["id"], PURPOSE))
        if held is None:
            raise Refusal(f"{story['identifier']} has no draft to take")
        if any(r.kind == queue.DROPPED_DRAFT for r in feature.records.get(story["id"], [])):
            raise Refusal(f"{story['identifier']} dropped its draft; its description stays as the story map wrote it")
        ops.ensure_issue_text(store, story["id"], story["title"], held["body"])
        record = Record(queue.TAKEN, story["id"], content_hash(held["body"]), "agent:captain", {})
        ops.ensure_record(store, story["id"], record)
        print(f"done: took the draft as the description of {story['identifier']}")

    def _drop(self, store: Store, feature: features.Feature, story: dict) -> None:
        records, ref = feature.records.get(story["id"], []), story["identifier"]
        saved = versions.held(records, queue.DRAFT, story["id"])
        if saved is None:
            raise Refusal(f"{ref} has no saved draft to drop")
        if any(r.kind == queue.TAKEN for r in records):
            raise Refusal(f"{ref} took its draft already; its description is the draft")
        ops.ensure_record(
            store, story["id"], Record(queue.DROPPED_DRAFT, story["id"], saved.version, "agent:captain", {})
        )
        print(f"done: dropped the draft of {ref}; its description stays as the story map wrote it")
