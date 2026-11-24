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
    reading,
    spikes,
    startable,
)
from deckhand.features import Feature
from deckhand.linear.client import LinearError
from deckhand.policy import Policy
from deckhand.records import HAND, Record
from deckhand.startable import DRAFT
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import DEFAULT_BASE, make_worktree, require_base, worktree_branch
from deckhand.store import Store
from deckhand.store.open import Stores

USAGE = (
    "hand takes [--team-key KEY]; context [--json], apply start REF, apply fill, apply wake REF,"
    " or apply spike --repository LABEL --file QUESTION.md"
)


@dataclass(frozen=True)
class Launch:
    story: dict
    records: list[Record]
    clone: Path
    branch: str
    argv: list[str]
    cwd: Path
    kind: str
    stop: str | None = None


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


def argv(name: str, ref: str, clone: Path, worktree: Path, resume: str | None, kind: str) -> list[str]:
    protected = launch.protected(config.config_dir(), answers.state_dir())
    settings = launch.hand_settings(ref, clone, worktree, Policy.load(clone), config.local(), protected, kind=kind)
    plugin = os.environ.get("DECKHAND_PLUGIN_DIR")
    prompt = launch.PROMPTS[kind].format(ref)
    return launch.hand_argv(name, settings, prompt, Path(plugin) if plugin else None, resume)


def start_record(story: dict, records: list[Record], kind: str) -> Record:
    newest = max((r.at for r in records), default="")
    data = {"kind": kind, "session": fleet.hand_name(story["identifier"])}
    return Record(asks.HAND_START, story["id"], newest, HAND, data)


def _resume(session: fleet.Session | None) -> str | None:
    return session.session_id if session is not None and session.session_id else None


@register("hand")
class Hand:
    def __init__(
        self, store: Store | None = None, runner: git.Runner = git.run, *, person: Store | None = None
    ) -> None:
        self._stores = Stores(app=store, person=person)
        self._runner = runner

    def _app(self) -> Store:
        return self._stores("app")

    def _person_store(self) -> Store:
        return self._stores("person")

    def _fleet(self) -> fleet.Fleet:
        read = fleet.sessions(self._runner)
        if read.note:
            raise Refusal(read.note)
        return read

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        if parsed["words"]:
            raise Refusal(f"context takes no REF; {USAGE}")
        store, local = self._app(), config.local()
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
                found = startable.startable(store, feature, me, local.clones)
                policy = reading.policy_for(notes, local.clones)
                work = [w for w in found if startable.due(w, feature, hands, policy)]
        facts = {
            "notes": notes,
            "feature": feature.project["name"] if feature is not None else None,
            "slots": {"used": fleet.slots_used(read.sessions), "cap": local.slot_cap},
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
        if session is not None and fleet.alive(session):
            print(f"{session.name} is already live")
            return
        person = self._person_store()
        me = claims.me(person)
        clones = config.local().clones
        reason = startable.why_not(store, feature, story, me, order.edges(store, feature), clones)
        if reason is not None:
            raise Refusal(reason)
        _free_slot(read)
        records = feature.records.get(story["id"], [])
        clone = startable.clone_of(story, clones)
        plan = self._prepare(story, records, clone, startable.kind(feature, story), session)
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
        local, used = config.local(), fleet.slots_used(read.sessions)
        cap = local.slot_cap
        hands = fleet.hands(read.sessions)
        wakes = []
        for story in feature.live:
            session, due = hands.get(story["identifier"]), startable.wake_due(feature, story)
            live = session is not None and fleet.live(session)
            slot = not (live and fleet.holds_slot(session))
            if story["assignee"] != me or due is None or (slot and used >= cap):
                continue
            if live or startable.clone_of(story, local.clones) is not None:
                wakes.append((story, *self._waking(feature, story, session, due)))
                used += slot
        plans = []
        policy = reading.policy_for([], local.clones)
        for work in startable.startable(store, feature, me, local.clones):
            if used >= cap or not startable.due(work, feature, hands, policy):
                continue
            story = work.story
            records = feature.records.get(story["id"], [])
            plans.append(self._prepare(story, records, work.clone, work.kind, hands.get(story["identifier"])))
            used += 1
        for story, wake, plan in wakes:
            self._serve(story, wake, plan, person, me)
        for plan in plans:
            self._launch(plan, person, me)
            print(f"started {fleet.hand_name(plan.story['identifier'])}")
        if used >= cap:
            print(f"no slot is free: {used} of {cap} in use")
        elif not plans and not wakes:
            print("nothing to start")

    def _wake(self, ref: str, team: str) -> None:
        read = self._fleet()
        store = self._app()
        feature = features.owning(store, team, ref)
        story = features.story(store, feature, ref)
        due = startable.wake_due(feature, story)
        if due is None:
            print("nothing to wake")
            return
        person = self._person_store()
        me = claims.me(person)
        claims.owned(story, me)
        session = fleet.hands(read.sessions).get(story["identifier"])
        live = session is not None and fleet.live(session)
        if not (live and fleet.holds_slot(session)) and fleet.slots_used(read.sessions) >= config.local().slot_cap:
            print("waits for a slot")
            return
        if not live and startable.clone_of(story, config.local().clones) is None:
            raise Refusal(f"no clone for {', '.join(story['labels'])} in {config.config_dir() / config.FILE}")
        self._serve(story, *self._waking(feature, story, session, due), person, me)

    def _waking(
        self, feature: Feature, story: dict, session: fleet.Session | None, due: Record
    ) -> tuple[Record, Launch | None]:
        wake = Record(asks.WAKE, story["id"], str(due.data.get("nonce") or due.version), HAND, {"decision": due.kind})
        if session is not None and fleet.live(session):
            return wake, None
        records = feature.records.get(story["id"], [])
        kind = startable.kind(feature, story)
        clone = startable.clone_of(story, config.local().clones)
        return wake, self._prepare(story, records, clone, kind, session)

    def _serve(self, story: dict, wake: Record, plan: Launch | None, person: Store, me: str) -> None:
        name = fleet.hand_name(story["identifier"])
        if plan is not None:
            self._launch(plan, person, me, wake)
            print(f"resumed {name}")
            return
        ops.ensure_record(self._app(), story["id"], wake)
        decision = wake.data["decision"]
        print(f"send to {name}: the person decided {decision} {wake.version[:8]}; carry on with the story")

    # A hand of another kind, a draft hand once the feature builds, never lends its session.
    def _prepare(
        self, story: dict, records: list[Record], clone: Path, kind: str, session: fleet.Session | None
    ) -> Launch:
        require_base(clone, self._runner)
        branch = worktree_branch(story, records)
        worktree = git.worktree_path(clone, branch)
        ref, launch_kind = story["identifier"], startable.launch_of(kind)
        resume = _resume(session) if queue.last_start(records, launch_kind) is not None else None
        hand_kind = launch.DRAFT if kind == DRAFT else launch.STORY
        found = argv(fleet.hand_name(ref), ref, clone, worktree, resume, hand_kind)
        stop = session.id if resume is not None and session is not None else None
        return Launch(story, records, clone, branch, found, worktree, launch_kind, stop)

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
        if session is not None and fleet.alive(session):
            print(f"{name} is already live")
            return
        _free_slot(read)
        require_base(clone, self._runner)
        worktree = git.worktree_path(clone, name)
        found = argv(name, key, clone, worktree, _resume(session), launch.SPIKE)
        asked = spikes.question_path(answers.state_dir(), key)
        asked.parent.mkdir(parents=True, exist_ok=True)
        asked.write_text(question, encoding="utf-8")
        git.worktree_detached(clone, name, DEFAULT_BASE, self._runner)
        self._retire(session.id if _resume(session) and session is not None else None)
        git.call(found, worktree, self._runner)
        print(f"started {name}")

    # The hand-start is written last, so no record of this launch reads as new since it.
    def _launch(self, plan: Launch, person: Store, me: str, wake: Record | None = None) -> None:
        store, story = self._app(), plan.story
        claims.claim(person, story, me)
        make_worktree(store, story, plan.clone, plan.branch, self._runner)
        if wake is not None:
            ops.ensure_record(store, story["id"], wake)
        ops.ensure_record(store, story["id"], start_record(story, plan.records, plan.kind))
        self._retire(plan.stop)
        git.call(plan.argv, plan.cwd, self._runner)

    # A resume opens a new session under the same name; a stop that fails leaves the resume to go ahead.
    def _retire(self, session: str | None) -> None:
        if session is None:
            return
        try:
            self._runner(["claude", "stop", session], None, None)
        except git.GitError:
            return


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
