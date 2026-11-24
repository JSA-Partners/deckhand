from __future__ import annotations

import json

from deckhand import answers, config, features, fleet, git, inputs, launch, spikes
from deckhand.policy import Policy
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import require_base
from deckhand.store import Store
from deckhand.store.local import default_path
from deckhand.store.open import Stores
from deckhand.store.state import LocalState

USAGE = "hand takes [--team-key KEY]; context [--json], or apply spike --repository LABEL --file QUESTION.md"


def _parse(args: list[str]) -> dict:
    found: dict = {"words": [], "json": False}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg in ("--team-key", "--repository", "--file"):
            found[arg[2:].replace("-", "_")] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    return found


@register("hand")
class Hand:
    def __init__(self, store: Store | None = None, runner: git.Runner = git.run) -> None:
        self._stores = Stores(app=store)
        self._runner = runner

    def _app(self) -> Store:
        return self._stores("app")

    def _fleet(self) -> fleet.Fleet:
        read = fleet.sessions(self._runner)
        if read.note:
            raise Refusal(read.note)
        return read

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        if parsed["words"]:
            raise Refusal(f"context takes no REF; {USAGE}")
        read = fleet.sessions(self._runner)
        feature = features.current(self._app(), team_key(parsed))
        held = [*sorted(fleet.hands(read.sessions).items()), *sorted(fleet.spikes(read.sessions).items())]
        facts = {
            "notes": [read.note] if read.note else [],
            "feature": feature.project["name"] if feature is not None else None,
            "live": [{"name": s.name, "state": s.state, "status": s.status} for _, s in held if fleet.live(s)],
        }
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        _print(facts)

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        if parsed["words"] != ["spike"]:
            raise Refusal(f"apply takes spike; {USAGE}")
        self._spike(team_key(parsed), parsed.get("repository", ""), parsed.get("file", ""))

    def _spike(self, team: str, label: str, path: str) -> None:
        question = spikes.question_of(inputs.read(path, "--file", USAGE))
        store = self._app()
        feature = features.open_feature(store, team)
        if feature is None:
            raise Refusal("no feature is open")
        if feature.stories:
            raise Refusal(
                f"{features.describe(feature.project)} already has stories; a spike runs before the story map"
            )
        clone = config.local().clones.get(label)
        if clone is None:
            raise Refusal(
                f"no clone for {label or 'a repository'} in {config.config_dir() / config.FILE}; pass --repository"
            )
        Policy.load(clone)
        read = self._fleet()
        key = spikes.key(feature.project["id"], question)
        name = fleet.spike_name(key)
        session = fleet.spikes(read.sessions).get(key)
        if session is not None and fleet.alive(session):
            print(f"{name} is already live")
            return
        _free_slot(read)
        require_base(clone, self._runner)
        asked = spikes.question_path(answers.state_dir(), key)
        asked.parent.mkdir(parents=True, exist_ok=True)
        asked.write_text(question, encoding="utf-8")
        with LocalState(default_path()) as rows:
            rows.want(name, key, "", launch.SPIKE, str(git.worktree_path(clone, name)))
        print("asked: the engine starts it")


def _free_slot(read: fleet.Fleet) -> None:
    cap, used = config.local().slot_cap, fleet.slots_used(read.sessions)
    if used >= cap:
        raise Refusal(f"no slot is free: {used} of {cap} in use")


def _print(facts: dict) -> None:
    for note in facts["notes"]:
        print(note)
    for session in facts["live"]:
        print(f"live: {session['name']} {session['state']}")
    if facts["feature"] is None:
        print("no feature is open")
