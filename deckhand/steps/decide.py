from __future__ import annotations

import json
import os
from pathlib import Path

from deckhand import answers, decisions, features, git, quiz, signoff, versions
from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.questions import as_tool_input
from deckhand.step import Refusal, register
from deckhand.steps.build import WORKTREE
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = (
    "decide takes --team-key KEY on both verbs; context KIND SUBJECT [--story REF ...] [--json],"
    " the SUBJECT a feature id, a story or a decision item's text, finding takes a story and --finding ID,"
    " quiz takes a story and --item N, review-cap takes a story and --stage plan|branch,"
    " sign-off takes a story;"
    " apply NONCE"
)


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


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
    if "item" in found:
        if not found["item"].isdigit() or int(found["item"]) < 1:
            raise Refusal(f"--item takes a quiz item number from 1; {USAGE}")
        found["item"] = int(found["item"])
    return found


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@register("decide")
class Decide:
    def __init__(
        self,
        store: Store | None = None,
        runner: git.Runner = git.run,
        repo: Path | None = None,
        reader: quiz.Reader = read_text,
    ) -> None:
        self._store = store
        self._runner = runner
        self._repo = repo
        self._reader = reader

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
        store = self._store_for_run(parsed)
        feature = features.open_feature(store, parsed.get("team_key", ""))
        if feature is None:
            raise Refusal("no feature is open")
        head = self._head(store, feature, subject) if kind == signoff.KIND else ""
        pin = decisions.pin(
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
        shown = self._missed_code(feature, pin) if kind == quiz.KIND else {}
        if parsed["json"]:
            print(json.dumps({"nonce": question.nonce, "tool_input": tool_input} | shown, indent=2))
            return
        if shown:
            print(f"where: {shown['where']}")
            print("\n".join(shown["code"]))
        print(f"nonce: {question.nonce}")
        print(json.dumps(tool_input, indent=2))

    def _head(self, store: Store, feature: features.Feature, ref: str) -> str:
        story = features.story(store, feature, ref)
        held = versions.held(feature.records.get(story["id"], []), WORKTREE, story["id"])
        name = held.data.get("branch") if held else None
        clone = self._repo or git.clone_root(Path.cwd(), self._runner)
        path = git.worktree_path(clone, name) if isinstance(name, str) and name else None
        if path is None or not path.is_dir():
            raise Refusal(f"{story['identifier']} has no worktree on this machine to sign off")
        return git.head(path, self._runner)

    def _missed_code(self, feature: features.Feature, pin: decisions.Pin) -> dict:
        story, _, n = pin.subject.rpartition(":")
        records = feature.records.get(story, [])
        if not quiz.missed(records, story, int(n)):
            return {}
        held = quiz.held_set(records, story)
        where = quiz.items_of(held)[int(n) - 1].where
        worktree = versions.held(records, WORKTREE, story)
        try:
            clone = self._repo or git.clone_root(Path.cwd(), self._runner)
        except git.GitError:
            clone = None
        branch = worktree.data.get("branch") if worktree else None
        if not isinstance(branch, str) or not branch or clone is None:
            return {"where": where, "code": ["the worktree is not on this machine"]}
        path = git.worktree_path(clone, branch)
        return {"where": where, "code": quiz.code_at(path, where, self._reader)}

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
            if pending.kind == quiz.KIND:
                held = versions.held(store.records(pending.stories[0]), quiz.KIND, pending.subject)
                verdict = "right" if held and held.data.get("correct") else "wrong, the item stays open"
                print(f"done: recorded quiz answer for {pending.subject}: {verdict}")
                return
            print(f"done: recorded {pending.kind} for {pending.subject} on {len(pending.stories)} stories")
            return
        decisions.pending_path(nonce, state).unlink(missing_ok=True)
        found = answers.find(nonce, pending.question, state)
        print(f"done: answered {found.answer if found else ''}, no {pending.kind} recorded")
