from __future__ import annotations

import json

from deckhand import answers, asks, chores, decisions, features, pins, queue, signoff, watch
from deckhand.questions import as_tool_input
from deckhand.step import Refusal, hand_role, register, team_key
from deckhand.steps.build import StoryStep
from deckhand.store.local import default_path
from deckhand.store.state import LocalState

USAGE = (
    "decide takes [--team-key KEY] on both verbs; context KIND SUBJECT [--story REF ...] [--json],"
    " the SUBJECT a feature id, a story or a decision item's text,"
    " finding takes a story or, for a feature finding, the feature id, and --finding ID,"
    " after-merge takes a story and --item N, question takes its id and one --story,"
    " config-edit takes a story and --item ID, stalled takes a story,"
    " review-cap takes a story and --stage plan|branch, or the feature id and --stage feature,"
    " sign-off and second-sign-off take a story;"
    " apply NONCE"
)


def _parse(args: list[str]) -> dict:
    found: dict = {"stories": [], "json": False, "words": []}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg in ("--story", "--team-key", "--finding", "--item", "--stage"):
            value = next(rest, "")
            if arg == "--story":
                found["stories"].append(value)
            else:
                found[arg[2:].replace("-", "_")] = value
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    return found


@register("decide")
class Decide(StoryStep):
    role = "person"

    def context(self, args: list[str]) -> None:
        if hand_role():
            raise Refusal("a hand never decides; record a question with ask apply REF --file QUESTION.json and stop")
        parsed = _parse(args)
        if len(parsed["words"]) != 2:
            raise Refusal(f"context takes KIND and SUBJECT; {USAGE}")
        kind, subject = parsed["words"]
        store = self._store_for_run()
        if kind in (chores.AFTER, asks.EDIT, queue.STALLED):
            feature = features.owning(store, team_key(parsed), subject)
        elif kind == asks.ANSWER and len(parsed["stories"]) == 1:
            feature = features.owning(store, team_key(parsed), parsed["stories"][0])
        else:
            feature = features.open_feature(store, team_key(parsed))
        if feature is None:
            raise Refusal("no feature is open")
        head = self._head(subject, team_key(parsed)) if kind == signoff.KIND else ""
        pin = pins.pin(
            store,
            feature,
            kind,
            subject,
            parsed["stories"],
            parsed.get("finding", ""),
            parsed.get("item"),
            parsed.get("stage", ""),
            head,
        )
        question = decisions.make_question(kind, pin)
        decisions.save_pending(question, pin, answers.state_dir())
        tool_input = as_tool_input(question)
        if parsed["json"]:
            print(json.dumps({"nonce": question.nonce, "tool_input": tool_input}, indent=2))
            return
        print(f"nonce: {question.nonce}")
        print(json.dumps(tool_input, indent=2))

    def _head(self, ref: str, team_key: str) -> str:
        at = self.locate(ref, team_key)
        if at.head is None:
            raise Refusal(f"{at.story['identifier']} has no worktree on this machine to sign off")
        return at.head

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        if len(parsed["words"]) != 1:
            raise Refusal(f"apply takes NONCE; {USAGE}")
        nonce = parsed["words"][0]
        state = answers.state_dir()
        pending = decisions.load_pending(nonce, state)
        store = self._store_for_run()
        if decisions.decide(store, pending, state, self.viewer()):
            decisions.pending_path(nonce, state).unlink(missing_ok=True)
            if pending.kind == features.APPROVAL:
                with LocalState(default_path()) as rows:
                    rows.drop(watch.HOLD)
            print(f"done: recorded {pending.kind} for {pending.subject} on {len(pending.stories)} stories")
            return
        decisions.pending_path(nonce, state).unlink(missing_ok=True)
        found = answers.find(nonce, pending.question, state)
        print(f"done: answered {found.answer if found else ''}, no {pending.kind} recorded")
