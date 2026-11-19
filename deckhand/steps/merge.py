from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from deckhand import gh, git, ops, signoff
from deckhand.records import Record
from deckhand.step import Refusal, register
from deckhand.steps.build import StoryStep, branch_of, parse
from deckhand.store import Store

USAGE = "merge takes REF and --team-key KEY on both verbs"
WAIT = 100
INTERVAL = 15


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
    ) -> None:
        super().__init__(store, runner, repo)
        self._sleep, self._clock = sleep, clock

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        at = self.locate(parsed["words"][0], parsed["team_key"])
        held = signoff.signed(at.records, at.story["id"])
        if held is None:
            print(f"{at.story['identifier']}: the person has not signed off")
            return
        head = signoff.signed_head(held)
        if at.head is not None and at.head != head:
            print(f"{at.story['identifier']}: head moved: the worktree is at {at.head}, signed at {head}")
            return
        standing = signoff.standing(at.records, at.story["id"], head)
        print(f"{at.story['identifier']}: {standing or f'signed off at {head}, ready to merge pinned to it'}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        at = self.locate(parsed["words"][0], parsed["team_key"])
        sid, ref = at.story["id"], at.story["identifier"]
        held = signoff.signed(at.records, sid)
        head = signoff.signed_head(held) if held else ""
        if held is None or not signoff.current(at.records, sid, head):
            reason = signoff.standing(at.records, sid, head) if held else "the person has not signed off"
            raise Refusal(f"{ref} has no current sign-off: {reason}")
        branch = branch_of(at.records, at.story)
        repo = gh.repo_name(at.clone, self._runner)
        pr = gh.pr_for(branch, repo, self._runner) if branch else None
        if at.head is not None and at.head != head:
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
        if pr is not None:
            gh.mark_draft(pr["number"], repo, self._runner)
        head = signoff.signed_head(sign)
        raise Refusal(f"the sign-off at {head} is void, {reason}; {story['identifier']} needs a new sign-off")
