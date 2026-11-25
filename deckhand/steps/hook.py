from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys

from deckhand import answers, decisions, features, fleet, gaps, launch, ops, spec
from deckhand.questions import HEADER_PREFIX, RECOMMENDED, valid_nonce
from deckhand.step import Refusal, hand_role, own_ref, register, team_key
from deckhand.steps.build import StoryStep
from deckhand.steps.captain import Captain
from deckhand.store import Store
from deckhand.store.local import default_path
from deckhand.store.state import LocalState
from deckhand.watch import HOLD

CAPTAIN_DENIED = "the captain runs deckhand commands only; check the fact with Read or Grep, or ask a spike"
BASH_DENIED = (
    "the captain runs deckhand commands only: deckhand, then words of letters, digits and _./:=@,+%-,"
    " 'single-quoted' text or \"double-quoted\" text without $ ` \\ !, separated by spaces;"
    " check the fact with Read or Grep, or ask a spike"
)
# Quoted text is printable ASCII, and double quotes also leave out $ ` \ ! since bash expands them there.
BARE = re.compile(r"""deckhand(?: +(?:[A-Za-z0-9_./:=@,+%-]+|'[ -&(-~]*'|"[ #%-\[\]-_a-~]*"))*""")
REASON = "A deckhand question must match its pending question and must not mark an option as recommended."
POINTERS = {launch.STORY: spec.pointer, launch.DRAFT: spec.draft_pointer}
USAGE = (
    "hook apply answer|ask|bash|permission reads one hook event as JSON on stdin; "
    "hook apply session reads one in the captain role and none for a hand"
)


def _read() -> dict:
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


# Unreadable input still starts the captain, so it counts as a fresh start.
def _source() -> object:
    try:
        return _read().get("source")
    except ValueError:
        return "startup"


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


# A request reaches this hook only when the captain's allow list does not hold the call.
def _deny_captain(hook_input: dict) -> None:
    if hook_input.get("hook_event_name") != "PermissionRequest" or hook_input.get("tool_name") == "AskUserQuestion":
        return
    _captain_denial()


def _captain_denial() -> None:
    decision = {"behavior": "deny", "message": CAPTAIN_DENIED}
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}))


def _pre_tool_denial(reason: str) -> None:
    output = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
    print(json.dumps({"hookSpecificOutput": output}))


def bare_deckhand(command: object) -> bool:
    return isinstance(command, str) and BARE.fullmatch(command) is not None


# The SDK host may skip PermissionRequest and auto-allow read-only commands, so the captain's Bash is checked here.
def _check_captain_bash(hook_input: dict) -> None:
    if not bare_deckhand((hook_input.get("tool_input") or {}).get("command")):
        _pre_tool_denial(BASH_DENIED)


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
        if _source() == "startup":
            with LocalState(default_path()) as rows:
                rows.drop(HOLD)
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
        if os.environ.get("DECKHAND_ROLE") == "captain":
            _deny_captain(hook_input)
            return
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
            if action == "bash" and os.environ.get("DECKHAND_ROLE") != "captain":
                return
            hook_input = _read()
            if action == "bash":
                _check_captain_bash(hook_input)
            elif action == "permission":
                self._permission(hook_input)
            elif action == "answer":
                answers.record(hook_input, answers.state_dir())
            elif action == "ask" and _denied(hook_input):
                _pre_tool_denial(REASON)
        except Exception as problem:
            if action == "permission" and os.environ.get("DECKHAND_ROLE") == "captain":
                _captain_denial()
            if action == "bash":
                _pre_tool_denial(BASH_DENIED)
            print(f"deckhand: hook {action} skipped: {type(problem).__name__}", file=sys.stderr)
