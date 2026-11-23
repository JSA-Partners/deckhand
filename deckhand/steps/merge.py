from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from deckhand import gh, git, ops, signoff
from deckhand.records import Record
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import Located, StoryStep, branch_of, parse
from deckhand.store import Store

USAGE = "merge takes REF and [--team-key KEY] on both verbs"
WAIT = 100
INTERVAL = 15
STILL_READY = "; the pull request may still be ready, run merge apply again to return it to draft"


def _parse(args: list[str]) -> dict:
    parsed = parse(args, USAGE)
    if len(parsed["words"]) != 1:
        raise Refusal(f"merge takes one story REF; {USAGE}")
    return parsed


@register("merge")
class Merge(StoryStep):
    def __init__(
        self,
        store: Store | None = None,
        runner: git.Runner = git.run,
        repo: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        *,
        me: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(store, runner, repo, me=me)
        self._sleep, self._clock = sleep, clock

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        merged = self.merged(parsed["words"][0], team_key(parsed))
        if merged is not None:
            print(merged)
            return
        at = self.locate(parsed["words"][0], team_key(parsed))
        held = signoff.signed(at.records, at.story["id"])
        if held is None:
            print(f"{at.story['identifier']}: the person has not signed off")
            return
        head = signoff.signed_head(held)
        if at.head is not None and at.head != head:
            print(f"{at.story['identifier']}: head moved: the worktree is at {at.head}, signed at {head}")
            return
        standing = signoff.standing(at.records, at.story["id"], head, at.policy.second_signoff)
        print(f"{at.story['identifier']}: {standing or f'signed off at {head}, ready to merge pinned to it'}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        at = self.locate(parsed["words"][0], team_key(parsed))
        self.owned(at)
        sid, ref = at.story["id"], at.story["identifier"]
        held = signoff.signed(at.records, sid)
        head = signoff.signed_head(held) if held else ""
        if held is None or not signoff.current(at.records, sid, head):
            reason = signoff.standing(at.records, sid, head) if held else "the person has not signed off"
            raise Refusal(f"{ref} has no current sign-off: {reason}{self._redraft(at, held)}")
        moved = at.head is not None and at.head != head
        if not moved and not signoff.current(at.records, sid, head, at.policy.second_signoff):
            raise Refusal(f"{ref} is waiting for a second sign-off")
        branch = branch_of(at.records, at.story)
        try:
            repo = gh.repo_name(at.clone, self._runner)
            pr = gh.pr_for(branch, repo, self._runner) if branch else None
        except gh.GhError:
            if not moved:
                raise
            repo, pr = "", None
        if moved:
            self._void(at.story, held, f"head moved: the worktree is at {at.head}", pr, repo)
        if pr is None:
            raise Refusal(f"{ref} has no open pull request")
        if pr["headRefOid"] != head:
            if git.is_ancestor(at.clone, pr["headRefOid"], head, self._runner):
                raise Refusal(f"the pull request is at {pr['headRefOid']}, behind {head}; push the signed head first")
            self._void(at.story, held, f"head moved: the pull request is at {pr['headRefOid']}", pr, repo)
        if pr["isDraft"]:
            gh.mark_ready(pr["number"], repo, self._runner)
        start = self._clock()
        while True:
            found = gh.checks(pr["number"], repo, self._runner)
            if found.state == "fail":
                self._void(at.story, held, "checks failed: " + ", ".join(found.failing), pr, repo)
            if found.state == "pass":
                break
            waited = self._clock() - start
            if waited >= WAIT:
                print(f"checks pending, run merge apply {ref} again")
                return
            self._sleep(min(INTERVAL, WAIT - waited))
        try:
            gh.merge_pinned(pr["number"], repo, head, self._runner)
        except gh.HeadMoved:
            self._void(at.story, held, "head moved: GitHub refused the merge pinned to the signed head", pr, repo)
        print(f"done: merged {ref} at {head}")

    def _void(self, story: dict, sign: Record, reason: str, pr: dict | None, repo: str) -> None:
        ops.ensure_record(self._store_for_run(), story["id"], signoff.void_record(story["id"], sign, reason))
        head = signoff.signed_head(sign)
        note = "" if pr is not None or repo else STILL_READY
        if pr is not None:
            note = self._draft(pr["number"], repo)
        raise Refusal(f"the sign-off at {head} is void, {reason}; {story['identifier']} needs a new sign-off{note}")

    def _draft(self, number: int, repo: str) -> str:
        try:
            gh.mark_draft(number, repo, self._runner)
        except gh.GhError:
            return STILL_READY
        return ""

    def _redraft(self, at: Located, held: Record | None) -> str:
        if held is None or signoff.voided(at.records, at.story["id"], held) is None:
            return ""
        branch = branch_of(at.records, at.story)
        try:
            repo = gh.repo_name(at.clone, self._runner)
            pr = gh.pr_for(branch, repo, self._runner) if branch else None
        except gh.GhError:
            return STILL_READY
        return self._draft(pr["number"], repo) if pr is not None and not pr["isDraft"] else ""
