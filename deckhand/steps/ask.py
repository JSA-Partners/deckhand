from __future__ import annotations

import json

from deckhand import asks, claims, features, inputs, ops
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import StoryStep, parse

USAGE = (
    "ask takes REF and [--team-key KEY]; context [--json], apply --file QUESTION.json, or apply --edit --file EDIT.json"
)


def _read(path: str) -> str:
    return inputs.read(path, "--file", USAGE)


def _arguments(args: list[str]) -> dict:
    rest, flags = [], {"edit": False, "file": ""}
    it = iter(args)
    for arg in it:
        if arg == "--edit":
            flags["edit"] = True
        elif arg == "--file":
            flags["file"] = next(it, "")
        else:
            rest.append(arg)
    parsed = parse(rest, USAGE)
    if len(parsed["words"]) != 1:
        raise Refusal(f"ask takes one story REF; {USAGE}")
    return parsed | flags


def _status(answer: object) -> str:
    return answer if isinstance(answer, str) else "open"


@register("ask")
class Ask(StoryStep):
    def _find(self, parsed: dict) -> tuple[dict, list]:
        store = self._store_for_run()
        feature = features.owning(store, team_key(parsed), parsed["words"][0])
        story = features.story(store, feature, parsed["words"][0])
        return story, feature.records.get(story["id"], [])

    def context(self, args: list[str]) -> None:
        parsed = _arguments(args)
        story, records = self._find(parsed)
        questions = [{"id": r.subject.rpartition(":")[2]} | r.data for r in records if r.kind == asks.QUESTION]
        edits = [{"id": r.subject.rpartition(":")[2]} | r.data for r in records if r.kind == asks.EDIT_REQUEST]
        for held in questions:
            held["answer"] = _answer(records, held["id"], asks.ANSWER)
        for held in edits:
            held["answer"] = _answer(records, held["id"], asks.EDIT)
        shape = {"question": asks.QUESTION_SHAPE, "edit": asks.EDIT_SHAPE}
        if parsed["json"]:
            print(
                json.dumps(
                    {"story": story["identifier"], "shape": shape, "questions": questions, "edits": edits}, indent=2
                )
            )
            return
        print(f"ask {story['identifier']}: write a question as {json.dumps(asks.QUESTION_SHAPE)}")
        print(f"or a change to Claude Code's own config as {json.dumps(asks.EDIT_SHAPE)} with --edit")
        for held in questions:
            print(f"{held['id']} {held['question']} {_status(held['answer'])}")
        for held in edits:
            print(f"{held['id']} {held['path']} {_status(held['answer'])}")

    def apply(self, args: list[str]) -> None:
        parsed = _arguments(args)
        story, _ = self._find(parsed)
        claims.owned(story, self.viewer())
        text = _read(parsed["file"])
        if parsed["edit"]:
            record = asks.edit_record(story["id"], asks.parse_edit(text))
        else:
            record = asks.question_record(story["id"], asks.parse_question(text))
        ops.ensure_record(self._store_for_run(), story["id"], record)
        print(f"done: recorded {record.kind} {record.subject.rpartition(':')[2]} on {story['identifier']}")


def _answer(records: list, subject: str, kind: str) -> str | None:
    found = asks.answer_of(records, subject, kind)
    return found.data["answer"] if found else None
