from __future__ import annotations

from deckhand import answers, features, git, inputs, ops, spikes
from deckhand.step import Refusal, register, team_key
from deckhand.store import Store
from deckhand.store.open import Stores

USAGE = "spike takes KEY and [--team-key KEY] on both verbs, and on apply --file ANSWER.md"
SHAPE = (
    "what was found",
    "how it was verified",
    "what could not be settled",
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
    if len(found["words"]) != 1:
        raise Refusal(f"spike takes one KEY; {USAGE}")
    return found


@register("spike")
class Spike:
    def __init__(self, store: Store | None = None, runner: git.Runner = git.run) -> None:
        self._stores = Stores(app=store)
        self._runner = runner

    def _store_for_run(self) -> Store:
        return self._stores("app")

    def context(self, args: list[str]) -> None:
        key = _parse(args)["words"][0]
        path = spikes.question_path(answers.state_dir(), key)
        try:
            question = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            question = "no question is saved for this key"
        print(f"spike: {key}")
        print(f"question: {question}")
        print("the answer holds, in this order:")
        for part in SHAPE:
            print(f"- {part}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        key = parsed["words"][0]
        text = inputs.read(parsed.get("file", ""), "--file", USAGE)
        if not text.strip():
            raise Refusal("the answer is empty")
        store = self._store_for_run()
        feature = features.open_feature(store, team_key(parsed))
        if feature is None:
            raise Refusal("no feature is open")
        project = feature.project["id"]
        if key not in spikes.keys(answers.state_dir(), project):
            raise Refusal(f"no spike {key} was asked in {feature.project['name']}")
        question = spikes.question_path(answers.state_dir(), key).read_text(encoding="utf-8")
        ops.ensure_document(store, project, "spike:" + key, project, spikes.TITLE + question, text)
        print(f"done: saved the answer to spike {key}")
