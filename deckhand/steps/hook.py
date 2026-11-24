from __future__ import annotations

import contextlib
import io
import json
import os
import sys

from deckhand import answers, decisions, features, fleet, gaps, launch, ops, spec
from deckhand.questions import HEADER_PREFIX, RECOMMENDED, valid_nonce
from deckhand.step import Refusal, hand_role, own_ref, register, team_key
from deckhand.steps.build import StoryStep
from deckhand.steps.captain import Captain
from deckhand.store import Store
from deckhand.store.local import default_path
from deckhand.store.state import LocalState

REASON = "A deckhand question must match its pending question and must not mark an option as recommended."
POINTERS = {launch.STORY: spec.pointer, launch.DRAFT: spec.draft_pointer}
USAGE = "hook apply answer|ask|permission reads one hook event as JSON on stdin; hook apply session|stop reads none"


def _read() -> dict:
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _deckhand_items(hook_input: dict) -> list[dict]:
    found = []
    for item in (hook_input.get("tool_input") or {}).get("questions") or []:
        header = str(item.get("header", ""))
        if not header.startswith(HEADER_PREFIX):
            continue
        if not valid_nonce(header[len(HEADER_PREFIX) :]):
            print("deckhand: hook ask ignored a deckhand header with a malformed nonce", file=sys.stderr)
            continue
        found.append(item)
    return found


def _recommended(items: list[dict]) -> bool:
    return any(
        RECOMMENDED.search(f"{o.get('label', '')}\n{o.get('description', '')}")
        for item in items
        for o in item.get("options") or []
    )


def _unpending(items: list[dict]) -> bool:
    state = answers.state_dir()
    for item in items:
        try:
            pending = decisions.load_pending(str(item["header"])[len(HEADER_PREFIX) :], state)
        except Refusal:
            return True
        labels = [o.get("label") for o in item.get("options") or []]
        if item.get("question") != pending.question or labels != pending.options:
            return True
    return False


def _denied(hook_input: dict) -> bool:
    items = _deckhand_items(hook_input)
    if not items:
        return False
    return "answers" in (hook_input.get("tool_input") or {}) or _recommended(items) or _unpending(items)


@register("hook")
class Hook(StoryStep):
    def __init__(self, store: Store | None = None, captain: Captain | None = None) -> None:
        super().__init__(store)
        self._captain = captain

    def _session(self) -> None:
        if hand_role():
            pointer = POINTERS.get(os.environ.get("DECKHAND_HAND_KIND", ""))
            ref = os.environ.get("DECKHAND_STORY", "")
            if pointer is not None and ref:
                print(pointer(ref))
            return
        if os.environ.get("DECKHAND_ROLE") != "captain":
            return
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            (self._captain or Captain()).context([])
        print(buffer.getvalue(), end="")

    def _stop(self) -> None:
        ref = own_ref()
        if hand_role() and ref:
            with LocalState(default_path()) as rows:
                rows.turn_ended(fleet.session_of(ref))

    def _permission(self, hook_input: dict) -> None:
        ref = os.environ.get("DECKHAND_STORY", "")
        if not hand_role() or not ref:
            return
        store = self._store_for_run()
        feature = features.owning(store, team_key({}), ref)
        story = features.story(store, feature, ref)
        command = (hook_input.get("tool_input") or {}).get("command")
        fields = (
            hook_input.get("hook_event_name"),
            hook_input.get("tool_name"),
            command,
            hook_input.get("reason") or hook_input.get("message"),
        )
        event, tool, command, reason = (field if isinstance(field, str) else "" for field in fields)
        ops.ensure_record(store, story["id"], gaps.record(story["id"], event, tool, command, reason))

    def context(self, args: list[str]) -> None:
        print(USAGE)

    def apply(self, args: list[str]) -> None:
        action = args[0] if args else ""
        try:
            if action == "session":
                self._session()
                return
            if action == "stop":
                self._stop()
                return
            hook_input = _read()
            if action == "permission":
                self._permission(hook_input)
            elif action == "answer":
                answers.record(hook_input, answers.state_dir())
            elif action == "ask" and _denied(hook_input):
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": REASON,
                            }
                        }
                    )
                )
        except Exception as problem:
            print(f"deckhand: hook {action} skipped: {type(problem).__name__}", file=sys.stderr)
