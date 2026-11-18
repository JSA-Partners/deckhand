from __future__ import annotations

import json
import re
import sys

from deckhand import answers, decisions
from deckhand.questions import HEADER_PREFIX, valid_nonce
from deckhand.step import Refusal, register

REASON = "A deckhand question must match its pending question and must not mark an option as recommended."
USAGE = "hook apply answer|ask reads one hook event as JSON on stdin"
RECOMMENDED = re.compile(r"\brecommended\b", re.IGNORECASE)


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


# A description reads 'kind: subject'; the subject is a title the person wrote.
def _recommended(items: list[dict]) -> bool:
    return any(
        RECOMMENDED.search(f"{o.get('label', '')}\n{str(o.get('description', '')).partition(':')[0]}")
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
class Hook:
    def context(self, args: list[str]) -> None:
        print(USAGE)

    def apply(self, args: list[str]) -> None:
        action = args[0] if args else ""
        try:
            hook_input = _read()
            if action == "answer":
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
