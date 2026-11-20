from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from deckhand import (
    answers,
    asks,
    claims,
    config,
    features,
    fleet,
    git,
    inputs,
    launch,
    ops,
    order,
    queue,
    spikes,
    startable,
)
from deckhand.credentials import Credentials
from deckhand.linear.client import LinearError
from deckhand.linear.connect import connect
from deckhand.policy import Policy
from deckhand.records import HAND, Record
from deckhand.startable import AFTER, BUILD, DRAFT
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import DEFAULT_BASE, make_worktree, require_base, worktree_branch
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = (
    "hand takes [--team-key KEY]; context [--json], apply start REF, apply fill, apply wake REF,"
    " or apply spike --repository LABEL --file QUESTION.md"
)
PROMPTS = {BUILD: "/deckhand:story {}", AFTER: "/deckhand:story {}", DRAFT: "/deckhand:draft {}"}
SPIKE_PROMPT = "/deckhand:spike {}"


@dataclass(frozen=True)
class Launch:
    story: dict
    records: list[Record]
    clone: Path
    branch: str
    argv: list[str]
    cwd: Path
    kind: str


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


def _resume(session: fleet.Session | None) -> str | None:
    return session.session_id if session is not None and session.session_id else None


@register("hand")
class Hand:
    def __init__(
        self, store: Store | None = None, runner: git.Runner = git.run, *, person: Store | None = None
    ) -> None:
        self._store = store
        self._runner = runner
        self._person = person

    def _app(self) -> Store:
        if self._store is not None:
            return self._store
        return LinearStore(connect(Credentials.load(config.config_dir()), "app"))

    def _person_store(self) -> Store:
        return self._person if self._person is not None else claims.person_store()

    def _fleet(self) -> fleet.Fleet:
        read = fleet.sessions(self._runner)
        if read.note:
            raise Refusal(read.note)
        return read

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        if parsed["words"]:
            raise Refusal(f"context takes no REF; {USAGE}")
        store, cap = self._app(), config.local().slot_cap
        read = fleet.sessions(self._runner)
        feature = features.current(store, team_key(parsed))
        hands = fleet.hands(read.sessions)
        held = [*sorted(hands.items()), *sorted(fleet.spikes(read.sessions).items())]
        live = [s for _, s in held if fleet.live(s)]
        notes, work = [read.note] if read.note else [], []
        try:
            me = claims.me(self._person_store())
        except (Refusal, LinearError) as error:
            notes.append(str(error))
        else:
            if feature is not None:
                work = [w for w in startable.startable(store, feature, me) if startable.due(w, feature, hands)]
        facts = {
            "notes": notes,
            "feature": feature.project["name"] if feature is not None else None,
            "slots": {"used": fleet.slots_used(read.sessions), "cap": cap},
            "live": [{"name": s.name, "state": s.state, "status": s.status} for s in live],
            "startable": [{"ref": w.story["identifier"], "title": w.story["title"], "work": w.kind} for w in work],
        }
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        _print(facts)

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        words = parsed["words"]
        if words[:1] == ["fill"] and len(words) == 1:
            self._fill(team_key(parsed))
        elif words[:1] in (["start"], ["wake"]) and len(words) == 2:
            (self._start if words[0] == "start" else self._wake)(words[1], team_key(parsed))
        elif words == ["spike"]:
            self._spike(team_key(parsed), parsed.get("repository", ""), parsed.get("file", ""))
        else:
            raise Refusal(f"apply takes start REF, fill, wake REF, or spike; {USAGE}")

    def _start(self, ref: str, team: str) -> None:
        read = self._fleet()
        store = self._app()
        feature = features.owning(store, team, ref)
        story = features.story(store, feature, ref)
        session = fleet.hands(read.sessions).get(story["identifier"])
        if session is not None and fleet.live(session):
            print(f"{session.name} is already live")
            return
        person = self._person_store()
        me = claims.me(person)
        reason = startable.why_not(store, feature, story, me, order.edges(store, feature))
        if reason is not None:
            raise Refusal(reason)
        _free_slot(read)
        records = feature.records.get(story["id"], [])
        plan = self._prepare(story, records, startable.clone_of(story), startable.kind(feature, story), session)
        self._launch(plan, person, me)
        print(f"started {fleet.hand_name(story['identifier'])}")

    def _fill(self, team: str) -> None:
        read = self._fleet()
        store = self._app()
        feature = features.current(store, team)
        if feature is None:
            print("no feature is open")
            return
        person = self._person_store()
        me = claims.me(person)
        cap, used = config.local().slot_cap, fleet.slots_used(read.sessions)
        hands = fleet.hands(read.sessions)
        plans = []
        for work in startable.startable(store, feature, me):
            if used >= cap or not startable.due(work, feature, hands):
                continue
            story = work.story
            records = feature.records.get(story["id"], [])
            session = hands.get(story["identifier"])
            plans.append(self._prepare(story, records, work.clone, work.kind, session))
            used += 1
        for plan in plans:
            self._launch(plan, person, me)
            print(f"started {fleet.hand_name(plan.story['identifier'])}")
        if used >= cap:
            print(f"no slot is free: {used} of {cap} in use")
        elif not plans:
            print("nothing to start")

    def _wake(self, ref: str, team: str) -> None:
        read = self._fleet()
        store = self._app()
        feature = features.owning(store, team, ref)
        story = features.story(store, feature, ref)
        records = feature.records.get(story["id"], [])
        due = startable.wake_due(feature, story)
        if due is None:
            print("nothing to wake")
            return
        name = fleet.hand_name(story["identifier"])
        wake = Record(asks.WAKE, story["id"], str(due.data.get("nonce") or due.version), HAND, {"decision": due.kind})
        person = self._person_store()
        me = claims.me(person)
        claims.owned(story, me)
        session = fleet.hands(read.sessions).get(story["identifier"])
        live = session is not None and fleet.live(session)
        if not (live and fleet.holds_slot(session)) and fleet.slots_used(read.sessions) >= config.local().slot_cap:
            print("waits for a slot")
            return
        if live:
            ops.ensure_record(store, story["id"], wake)
            print(f"send to {name}: the person decided the {due.kind} you waited on; carry on with the story")
            return
        clone = startable.clone_of(story)
        if clone is None:
            raise Refusal(f"no clone for {', '.join(story['labels'])} in {config.config_dir() / config.FILE}")
        plan = self._prepare(story, records, clone, startable.kind(feature, story), session)
        self._launch(plan, person, me, wake)
        print(f"resumed {name}")

    def _argv(
        self, name: str, ref: str, clone: Path, worktree: Path, prompt: str, resume: str | None, spike: bool = False
    ) -> list[str]:
        protected = launch.protected(config.config_dir(), answers.state_dir())
        settings = launch.hand_settings(
            ref, clone, worktree, Policy.load(clone), config.local(), protected, spike=spike
        )
        plugin = os.environ.get("DECKHAND_PLUGIN_DIR")
        return launch.hand_argv(name, settings, prompt, Path(plugin) if plugin else None, resume)

    # A hand of another kind, a draft hand once the feature builds, never lends its session.
    def _prepare(
        self, story: dict, records: list[Record], clone: Path, kind: str, session: fleet.Session | None
    ) -> Launch:
        require_base(clone, self._runner)
        branch = worktree_branch(story, records)
        worktree = git.worktree_path(clone, branch)
        ref, launch_kind = story["identifier"], startable.launch_of(kind)
        resume = _resume(session) if queue.last_start(records, launch_kind) is not None else None
        argv = self._argv(fleet.hand_name(ref), ref, clone, worktree, PROMPTS[kind].format(ref), resume)
        return Launch(story, records, clone, branch, argv, worktree, launch_kind)

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
        read = self._fleet()
        key = spikes.key(feature.project["id"], question)
        name = fleet.spike_name(key)
        session = fleet.spikes(read.sessions).get(key)
        if session is not None and fleet.live(session):
            print(f"{name} is already live")
            return
        _free_slot(read)
        require_base(clone, self._runner)
        worktree = git.worktree_path(clone, name)
        argv = self._argv(name, key, clone, worktree, SPIKE_PROMPT.format(key), _resume(session), spike=True)
        asked = spikes.question_path(answers.state_dir(), key)
        asked.parent.mkdir(parents=True, exist_ok=True)
        asked.write_text(question, encoding="utf-8")
        git.worktree_detached(clone, name, DEFAULT_BASE, self._runner)
        git.call(argv, worktree, self._runner)
        print(f"started {name}")

    # The hand-start is written last, so no record of this launch reads as new since it.
    def _launch(self, plan: Launch, person: Store, me: str, wake: Record | None = None) -> None:
        store, story = self._app(), plan.story
        claims.claim(person, story, me)
        make_worktree(store, story, plan.clone, plan.branch, self._runner)
        if wake is not None:
            ops.ensure_record(store, story["id"], wake)
        newest = max((r.at for r in plan.records), default="")
        data = {"kind": plan.kind, "session": fleet.hand_name(story["identifier"])}
        ops.ensure_record(store, story["id"], Record(asks.HAND_START, story["id"], newest, HAND, data))
        git.call(plan.argv, plan.cwd, self._runner)


def _free_slot(read: fleet.Fleet) -> None:
    cap, used = config.local().slot_cap, fleet.slots_used(read.sessions)
    if used >= cap:
        raise Refusal(f"no slot is free: {used} of {cap} in use")


def _print(facts: dict) -> None:
    for note in facts["notes"]:
        print(note)
    print(f"slots: {facts['slots']['used']} of {facts['slots']['cap']}")
    for session in facts["live"]:
        print(f"live: {session['name']} {session['state']}")
    if facts["feature"] is None:
        print("no feature is open")
    print("startable:" if facts["startable"] else "startable: none")
    for work in facts["startable"]:
        print(f"- {work['ref']} {work['work']}")
