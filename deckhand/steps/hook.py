from __future__ import annotations

import json
import sys

from deckhand import answers, decisions
from deckhand.questions import HEADER_PREFIX
from deckhand.step import Refusal, register

REASON = "A deckhand question must match its pending question and must not mark an option as recommended."
USAGE = "hook apply answer|ask reads one hook event as JSON on stdin"


def _read() -> dict:
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _recommended(hook_input: dict) -> bool:
    for item in (hook_input.get("tool_input") or {}).get("questions") or []:
        if not str(item.get("header", "")).startswith(HEADER_PREFIX):
            continue
        if any("recommended" in str(o.get("label", "")).lower() for o in item.get("options") or []):
            return True
    return False


def _unpending(hook_input: dict) -> bool:
    state = answers.state_dir()
    for item in (hook_input.get("tool_input") or {}).get("questions") or []:
        header = str(item.get("header", ""))
        if not header.startswith(HEADER_PREFIX):
            continue
        try:
            pending = decisions.load_pending(header[len(HEADER_PREFIX) :], state)
        except Refusal:
            return True
        labels = [o.get("label") for o in item.get("options") or []]
        if item.get("question") != pending.question or labels != pending.options:
            return True
    return False


@register("hook")
class Hook:
    def context(self, args: list[str]) -> None:
        print(USAGE)

    def apply(self, args: list[str]) -> None:
        action = args[0] if args else ""
        try:
            hook_input = _read()
            if action == "answer":
                answers.record(hook_input, answers.state_dir())
            elif action == "ask" and (_recommended(hook_input) or _unpending(hook_input)):
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
