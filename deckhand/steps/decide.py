from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from deckhand import answers, asks, claims, decisions, features, git, pins, quiz, review, signoff, versions
from deckhand.questions import as_tool_input
from deckhand.step import Refusal, hand_role, register, team_key
from deckhand.steps.build import WORKTREE, StoryStep
from deckhand.store import Store

USAGE = (
    "decide takes [--team-key KEY] on both verbs; context KIND SUBJECT [--story REF ...] [--json],"
    " the SUBJECT a feature id, a story or a decision item's text, finding takes a story and --finding ID,"
    " quiz and after-merge take a story and --item N, question takes its id and one --story,"
    " config-edit takes a story and --item ID,"
    " review-cap takes a story and --stage plan|branch,"
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


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@register("decide")
class Decide(StoryStep):
    def __init__(
        self,
        store: Store | None = None,
        runner: git.Runner = git.run,
        repo: Path | None = None,
        reader: quiz.Reader = read_text,
        *,
        me: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(store, runner, repo, me=me)
        self._reader = reader

    def _store_for_run(self) -> Store:
        return self._store if self._store is not None else claims.person_store()

    def context(self, args: list[str]) -> None:
        if hand_role():
            raise Refusal("a hand never decides; record a question with ask apply REF --file QUESTION.json and stop")
        parsed = _parse(args)
        if len(parsed["words"]) != 2:
            raise Refusal(f"context takes KIND and SUBJECT; {USAGE}")
        kind, subject = parsed["words"]
        store = self._store_for_run()
        if kind == signoff.AFTER:
            feature = features.owning(store, team_key(parsed), subject)
        elif kind == asks.ANSWER and len(parsed["stories"]) == 1:
            feature = features.owning(store, team_key(parsed), parsed["stories"][0])
        else:
            feature = features.open_feature(store, team_key(parsed))
        if feature is None:
            raise Refusal("no feature is open")
        wants_head = kind == signoff.KIND or (kind == review.CAP_DECISION and parsed.get("stage") == "branch")
        head = self._head(subject, team_key(parsed)) if wants_head else ""
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
        shown = self._missed_code(feature, pin) if kind == quiz.KIND else {}
        if parsed["json"]:
            print(json.dumps({"nonce": question.nonce, "tool_input": tool_input} | shown, indent=2))
            return
        if shown:
            print(f"where: {shown['where']}")
            print("\n".join(shown["code"]))
        print(f"nonce: {question.nonce}")
        print(json.dumps(tool_input, indent=2))

    def _head(self, ref: str, team_key: str) -> str:
        at = self.locate(ref, team_key)
        if at.head is None:
            raise Refusal(f"{at.story['identifier']} has no worktree on this machine to sign off")
        return at.head

    def _missed_code(self, feature: features.Feature, pin: decisions.Pin) -> dict:
        story, _, n = pin.subject.rpartition(":")
        labels = next((other["labels"] for other in feature.live if other["id"] == story), [])
        return self._code(feature.records.get(story, []), story, int(n), labels)

    def _code(self, records: list, story: str, n: int, labels: list[str]) -> dict:
        if not quiz.missed(records, story, n):
            return {}
        held = quiz.held_set(records, story)
        where = quiz.items_of(held)[n - 1].where
        worktree = versions.held(records, WORKTREE, story)
        try:
            clone = self._clone(labels)
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
        store = self._store_for_run()
        if decisions.decide(store, pending, state, self.viewer()):
            decisions.pending_path(nonce, state).unlink(missing_ok=True)
            if pending.kind == quiz.KIND:
                records = store.records(pending.stories[0])
                held = versions.held(records, quiz.KIND, pending.subject)
                verdict = "right" if held and held.data.get("correct") else "wrong, the item stays open"
                print(f"done: recorded quiz answer for {pending.subject}: {verdict}")
                story, _, n = pending.subject.rpartition(":")
                shown = self._code(records, story, int(n), store.issue(story)["labels"])
                if shown:
                    print(f"where: {shown['where']}")
                    print("\n".join(shown["code"]))
                return
            print(f"done: recorded {pending.kind} for {pending.subject} on {len(pending.stories)} stories")
            return
        decisions.pending_path(nonce, state).unlink(missing_ok=True)
        found = answers.find(nonce, pending.question, state)
        print(f"done: answered {found.answer if found else ''}, no {pending.kind} recorded")
