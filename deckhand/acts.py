from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from pathlib import Path

from deckhand import answers, asks, claims, config, fleet, git, launch, ops, queue
from deckhand.actions import Action, Adopt, Launch, Post, Remove, Resume, Stop
from deckhand.linear.client import LinearError
from deckhand.policy import Policy
from deckhand.step import Refusal
from deckhand.steps.build import DEFAULT_BASE, make_worktree, require_base, worktree_branch
from deckhand.store import Store
from deckhand.store.local import LocalRecords
from deckhand.store.state import LocalState

UNHEARD = "unheard"


def unheard(state: LocalState) -> frozenset[int]:
    body = state.get(UNHEARD)
    return frozenset(body["seqs"]) if body is not None else frozenset()


def argv(name: str, ref: str, clone: Path, worktree: Path, kind: str) -> list[str]:
    protected = launch.protected(config.config_dir(), answers.state_dir())
    settings = launch.hand_settings(ref, clone, worktree, Policy.load(clone), config.local(), protected, kind=kind)
    plugin = os.environ.get("DECKHAND_PLUGIN_DIR")
    prompt = launch.PROMPTS[kind].format(ref)
    return launch.hand_argv(name, settings, prompt, Path(plugin) if plugin else None)


def _one_line(text: str) -> str:
    return "; ".join(line.strip() for line in text.splitlines() if line.strip())


def _heard_not(state: LocalState, seqs: Sequence[int]) -> None:
    pending = {m.seq for m in state.pending()}
    state.put(UNHEARD, {"seqs": sorted((unheard(state) | set(seqs)) & pending)})


class Acts:
    def __init__(
        self, state: LocalState, records: LocalRecords, person: Callable[[], Store], runner: git.Runner = git.run
    ) -> None:
        self._state = state
        self._records = records
        self._person = person
        self._runner = runner

    def run(self, action: Action) -> str | None:
        try:
            self._run(action)
        except Refusal as refusal:
            problem = refusal.rule
        except git.GitError as error:
            problem = f"{error.command} failed: {error.stderr_tail}"
        except LinearError as error:
            problem = f"{error.code}: {error.message}"
        except Exception as error:
            problem = type(error).__name__
        else:
            return None
        return f"{fleet.ref_of(action.name)}: {_one_line(problem)}"

    def _run(self, action: Action) -> None:
        if isinstance(action, Launch):
            self._launch(action)
        elif isinstance(action, Resume):
            self._resume(action)
        elif isinstance(action, Post):
            self._post(action)
        elif isinstance(action, Stop):
            self._claude("stop", action.short)
            if action.seqs:
                _heard_not(self._state, action.seqs)
        elif isinstance(action, Remove):
            if action.stop:
                self._claude("stop", action.short)
            self._claude("rm", action.short)
        elif isinstance(action, Adopt):
            self._state.adopt(action.name, action.short, action.session)

    def _claude(self, verb: str, short: str) -> str:
        return git.call(["claude", verb, short], None, self._runner)

    def _alive(self, name: str) -> fleet.Session | None:
        read = fleet.sessions(self._runner)
        if read.note:
            raise Refusal(read.note)
        found = [s for s in read.sessions if s.name == name and fleet.alive(s)]
        return max(found, key=lambda s: s.started_at, default=None)

    def _short(self, name: str, output: str) -> str | None:
        short, _ = launch.backgrounded(output)
        if short is None:
            session = self._alive(name)
            short = session.id if session is not None else None
        return short

    # A first hand-start lands before the claim, so a failed claim never reads as held elsewhere.
    def _launch(self, action: Launch) -> None:
        story = action.story
        records = self._records.records(story["id"]) if story is not None else []
        branch = action.name if story is None else worktree_branch(story, records)
        worktree = git.worktree_path(action.clone, branch)
        self._state.want(action.name, action.ref, story["id"] if story else "", action.kind, str(worktree))
        row = self._state.hands().get(action.name)
        session = self._alive(action.name)
        if session is not None:
            self._state.adopt(action.name, session.id, session.session_id)
            return
        try:
            git.fetch(action.clone, self._runner)
        except git.GitError as error:
            raise Refusal(f"cannot fetch origin: {error.stderr_tail}") from None
        require_base(action.clone, self._runner)
        command = argv(action.name, action.ref, action.clone, worktree, action.kind)
        start = asks.hand_start(story, records, action.kind) if story is not None else None
        if story is None:
            git.worktree_detached(action.clone, action.name, DEFAULT_BASE, self._runner)
        else:
            claims.owned(story, action.me)
            make_worktree(self._records, story, action.clone, branch, self._runner)
            if queue.last_start(records) is None:
                ops.ensure_record(self._records, story["id"], start)
            claims.claim(self._person(), story, action.me)
        output = git.call(command, worktree, self._runner)
        if start is not None:
            ops.ensure_record(self._records, start.subject, start)
        if action.restart and row is not None and row.short:
            self._state.restarted(action.name)
        short = self._short(action.name, output)
        if short is not None:
            self._state.launched(action.name, short)

    def _resume(self, action: Resume) -> None:
        if self._alive(action.name) is not None:
            return
        cwd = Path(action.cwd) if action.cwd else None
        output = git.call(launch.resume_argv(action.session, action.text), cwd, self._runner)
        copy = launch.backgrounded(output)[1]
        if copy is not None:
            self._claude("stop", copy)
            self._claude("rm", copy)
            raise Refusal(f"the session was already running; its copy {copy} is removed")
        self._state.delivered(action.seqs)
        if action.record is not None:
            ops.ensure_record(self._records, action.record.subject, action.record)
        if action.restart:
            self._state.restarted(action.name)
        short = self._short(action.name, output)
        if short is not None:
            self._state.launched(action.name, short)

    def _post(self, action: Post) -> None:
        if self._state.post(action.key, action.name, action.kind, action.body) and action.restart:
            self._state.restarted(action.name)
        if action.record is not None:
            ops.ensure_record(self._records, action.issue, action.record)
