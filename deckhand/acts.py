from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from deckhand import claims, fleet, git, launch, ops
from deckhand.linear.client import LinearError
from deckhand.reconcile import Action, Adopt, Launch, Post, Remove, Resume, Stop
from deckhand.step import Refusal
from deckhand.steps.build import DEFAULT_BASE, make_worktree, require_base, worktree_branch
from deckhand.steps.hand import argv, start_record
from deckhand.store import Store
from deckhand.store.local import LocalRecords
from deckhand.store.state import LocalState

UNHEARD = "unheard"


def unheard(state: LocalState) -> frozenset[int]:
    body = state.get(UNHEARD)
    return frozenset(body["seqs"]) if body is not None else frozenset()


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
        ref = fleet.ref_of(action.name)
        try:
            self._run(action)
        except Refusal as refusal:
            return f"{ref}: {refusal.rule}"
        except git.GitError as error:
            return f"{ref}: {error.command} failed: {error.stderr_tail}"
        except LinearError as error:
            return f"{ref}: {error.code}: {error.message}"
        except Exception as error:
            return f"{ref}: {type(error).__name__}"
        return None

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

    # The hand-start is the last record and lands before the claim, so a failed claim never reads as held elsewhere.
    def _launch(self, action: Launch) -> None:
        story = action.story
        records = self._records.records(story["id"]) if story is not None else []
        branch = action.name if story is None else worktree_branch(story, records)
        worktree = git.worktree_path(action.clone, branch)
        self._state.want(action.name, action.ref, story["id"] if story else "", action.kind, str(worktree))
        session = self._alive(action.name)
        if session is not None:
            self._state.adopt(action.name, session.id, session.session_id)
            return
        try:
            git.fetch(action.clone, self._runner)
        except git.GitError as error:
            raise Refusal(f"cannot fetch origin: {error.stderr_tail}") from None
        require_base(action.clone, self._runner)
        command = argv(action.name, action.ref, action.clone, worktree, None, action.kind)
        if story is None:
            git.worktree_detached(action.clone, action.name, DEFAULT_BASE, self._runner)
        else:
            claims.owned(story, action.me)
            make_worktree(self._records, story, action.clone, branch, self._runner)
            ops.ensure_record(self._records, story["id"], start_record(story, records, action.kind))
            claims.claim(self._person(), story, action.me)
        short, _ = launch.backgrounded(git.call(command, worktree, self._runner))
        if short is None:
            session = self._alive(action.name)
            short = session.id if session is not None else None
        if short is not None:
            self._state.launched(action.name, short)
        if action.restart:
            self._state.restarted(action.name)

    def _resume(self, action: Resume) -> None:
        if self._alive(action.name) is not None:
            return
        cwd = Path(action.cwd) if action.cwd else None
        short, copy = launch.backgrounded(git.call(launch.resume_argv(action.session, action.text), cwd, self._runner))
        if copy is not None:
            self._claude("stop", copy)
            self._claude("rm", copy)
            raise Refusal(f"the session was already running; its copy {copy} is removed")
        self._state.delivered(action.seqs)
        if action.record is not None:
            ops.ensure_record(self._records, action.record.subject, action.record)
        if short is not None:
            self._state.launched(action.name, short)
        if action.restart:
            self._state.restarted(action.name)

    def _post(self, action: Post) -> None:
        if self._state.post(action.key, action.name, action.kind, action.body) and action.restart:
            self._state.restarted(action.name)
        if action.record is not None:
            ops.ensure_record(self._records, action.issue, action.record)
