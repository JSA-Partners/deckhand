from __future__ import annotations

import json
import os
from pathlib import Path

from deckhand import answers, decisions
from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.questions import as_tool_input
from deckhand.step import Refusal, register
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = (
    "decide context KIND SUBJECT --version V --story ID [--story ID ...] [--json]; decide apply NONCE --team-key KEY"
)


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


def _parse(args: list[str]) -> dict:
    found: dict = {"stories": [], "json": False, "words": []}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg in ("--version", "--story", "--team-key"):
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
class Decide:
    def __init__(self, store: Store | None = None) -> None:
        self._store = store

    def _store_for_run(self, parsed: dict) -> Store:
        if self._store is not None:
            return self._store
        if not parsed.get("team_key"):
            raise Refusal(f"--team-key is required; {USAGE}")
        return LinearStore(connect(Credentials.load(_config_dir()), "app"))

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        if len(parsed["words"]) != 2:
            raise Refusal(f"context takes KIND and SUBJECT; {USAGE}")
        kind, subject = parsed["words"]
        if not parsed.get("version") or not parsed["stories"]:
            raise Refusal(f"--version and at least one --story are required; {USAGE}")
        question = decisions.make_question(kind, subject, parsed["version"], parsed["stories"])
        decisions.save_pending(question, parsed["version"], parsed["stories"], answers.state_dir())
        tool_input = as_tool_input(question)
        if parsed["json"]:
            print(json.dumps({"nonce": question.nonce, "tool_input": tool_input}, indent=2))
            return
        print(f"nonce: {question.nonce}")
        print(json.dumps(tool_input, indent=2))

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        if len(parsed["words"]) != 1:
            raise Refusal(f"apply takes NONCE; {USAGE}")
        nonce = parsed["words"][0]
        state = answers.state_dir()
        pending = decisions.load_pending(nonce, state)
        store = self._store_for_run(parsed)
        if decisions.decide(store, pending, state):
            decisions.pending_path(nonce, state).unlink(missing_ok=True)
            print(f"done: recorded {pending.kind} for {pending.subject} on {len(pending.stories)} stories")
            return
        decisions.pending_path(nonce, state).unlink(missing_ok=True)
        found = answers.find(nonce, pending.question, state)
        print(f"done: answered {found.answer if found else ''}, no {pending.kind} recorded")
