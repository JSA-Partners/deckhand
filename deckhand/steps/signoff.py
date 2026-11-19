from __future__ import annotations

import json
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from deckhand import gh, git, implplan, ops, quiz, review, signoff, tuicr, versions
from deckhand.checks import KIND as CHECKS
from deckhand.checks import passing
from deckhand.policy import Policy
from deckhand.records import Record
from deckhand.step import Refusal, register
from deckhand.steps.build import DEFAULT_BASE, StoryStep, branch_of
from deckhand.store import Store
from deckhand.summary import parse_summary

USAGE = (
    "signoff takes REF and --team-key KEY on both verbs; context [--screen 1-5] [--json],"
    " apply summary --summary SUMMARY.json or apply review"
)
MERGE_USAGE = "merge takes REF and --team-key KEY on both verbs"
SCREENS = ("what changed", "evidence", "findings", "tuicr", "quiz")
ACTIONS = ("summary", "review")
WAIT = 100
INTERVAL = 15


def _parse(args: list[str], usage: str) -> dict:
    found: dict = {"words": [], "json": False}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg in ("--team-key", "--summary", "--screen"):
            found[arg[2:].replace("-", "_")] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {usage}")
        else:
            found["words"].append(arg)
    if not found.get("team_key"):
        raise Refusal(f"--team-key is required; {usage}")
    if not found["words"]:
        raise Refusal(f"a story REF is required; {usage}")
    return found


@dataclass
class Story:
    story: dict
    records: list[Record]
    clone: Path
    policy: Policy
    path: Path | None
    head: str | None


def _read(path: str) -> str:
    if not path:
        raise Refusal(f"--summary is required; {USAGE}")
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        raise Refusal(f"{path}: cannot be read") from None
    except UnicodeDecodeError:
        raise Refusal(f"{path}: not valid UTF-8") from None


def _declined(records: list[Record]) -> list[dict]:
    ids = review.declined(records)
    found: dict[str, dict] = {}
    for record in records:
        if record.kind == review.KIND:
            found |= {item["id"]: item for item in record.data.get("findings", []) if item["id"] in ids}
    return list(found.values())


def _finding(item: dict) -> str:
    return f"- {item['severity']} {item['id']} {item['lens']} {item['path']}:{item['line']} {item['summary']}"


class _Located(StoryStep):
    def _located(self, ref: str, team_key: str) -> Story:
        clone = self._clone()
        feature, story, policy = self._story(ref, team_key, clone)
        records = feature.records.get(story["id"], [])
        branch = branch_of(records, story)
        path = git.worktree_path(clone, branch) if branch else None
        if path is None or not path.is_dir():
            return Story(story, records, clone, policy, None, None)
        return Story(story, records, clone, policy, path, git.head(path, self._runner))


@register("signoff")
class Signoff(_Located):
    def context(self, args: list[str]) -> None:
        parsed = _parse(args, USAGE)
        at = self._located(parsed["words"][0], parsed["team_key"])
        screen = parsed.get("screen", "")
        if screen and screen not in ("1", "2", "3", "4", "5"):
            raise Refusal(f"--screen takes 1 to 5; {USAGE}")
        facts = self._screen(at, int(screen)) if screen else self._overview(at)
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        for line in facts["lines"]:
            print(line)

    def _overview(self, at: Story) -> dict:
        sid, head = at.story["id"], at.head
        if head is None:
            missing = [f"{at.story['identifier']} has no worktree on this machine"]
            return {"story": at.story["identifier"], "head": None, "missing": missing, "lines": missing}
        summary = signoff.held_summary(at.records, sid, head)
        built = _built(at)
        comments = self._comments(at)
        status = {
            "what changed": "saved" if summary else "no change summary at this head",
            "evidence": "checks pass" if passing(at.records, sid, head) else "checks are not passing",
            "findings": f"{len(_last_branch(at.records, at.story['id']).get('findings', []))} in the last branch round",
            "tuicr": "not built at this head" if built is None else _built_status(comments),
            "quiz": "passed" if quiz.passed(at.records, sid, head) else "not passed",
        }
        missing = signoff.gaps(at.records, sid, head)
        standing = signoff.standing(at.records, sid, head)
        missing += [standing] if standing else []
        lines = [f"sign-off for {at.story['identifier']} at {head}"]
        lines += [f"{n} {name}: {status[name]}" for n, name in enumerate(SCREENS, start=1)]
        lines += ["missing:", *(f"- {m}" for m in missing)] if missing else ["signed off at this head"]
        found = {"story": at.story["identifier"], "head": head, "screens": status, "missing": missing}
        return found | {"comments": comments, "lines": lines}

    def _screen(self, at: Story, n: int) -> dict:
        if at.head is None:
            return {"screen": n, "lines": [f"{at.story['identifier']} has no worktree on this machine"]}
        lines = [f"{n} {SCREENS[n - 1]} for {at.story['identifier']} at {at.head}"]
        if n == 4:
            comments = self._comments(at)
            return {"screen": n, "comments": comments, "lines": lines + _tuicr(at, comments)}
        lines += {1: _what_changed, 2: _evidence, 3: _findings, 5: _quiz}[n](at)
        return {"screen": n, "lines": lines}

    def _comments(self, at: Story) -> list[dict]:
        built = _built(at)
        if built is None or not built.get("sha") or at.path is None:
            return []
        try:
            session = tuicr.session_for(at.path, built["sha"], self._runner)
            found = tuicr.comments(at.path, session["slug"], self._runner) if session else []
        except (git.GitError, ValueError):
            return []
        return tuicr.persons(found, built.get("hunks", []))

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args, USAGE)
        if len(parsed["words"]) != 2 or parsed["words"][1] not in ACTIONS:
            raise Refusal(f"apply takes REF and summary or review; {USAGE}")
        at = self._located(parsed["words"][0], parsed["team_key"])
        if at.path is None or at.head is None:
            ref = at.story["identifier"]
            raise Refusal(f"{ref} has no worktree; run build apply {ref} worktree")
        if parsed["words"][1] == "summary":
            self._summary(at, parsed)
            return
        self._review(at)

    def _summary(self, at: Story, parsed: dict) -> None:
        summary = parse_summary(_read(parsed.get("summary", "")))
        if git.dirty(at.path, self._runner):
            raise Refusal(f"{at.path} has uncommitted changes; commit them first so the summary is pinned")
        record = signoff.summary_record(at.story["id"], at.head, summary)
        ops.ensure_record(self._store_for_run(), at.story["id"], record)
        print(f"done: saved the change summary for {at.story['identifier']} at {at.head}")

    def _review(self, at: Story) -> None:
        sid, head = at.story["id"], at.head
        summary = signoff.held_summary(at.records, sid, head)
        plan = versions.held(at.records, implplan.PURPOSE, sid)
        if summary is None or plan is None:
            raise Refusal(f"{at.story['identifier']} needs its plan and a change summary at {head} first")
        base = git.merge_base(at.path, "HEAD", DEFAULT_BASE, self._runner)
        doubtful = {
            f["path"]
            for f in _last_branch(at.records, at.story["id"]).get("findings", [])
            if f["confidence"] < review.DOUBTFUL
        }
        diff = git.diff(at.path, base, runner=self._runner)
        hunks = tuicr.select_hunks(diff, at.policy, doubtful, plan.data, summary)
        held = versions.held(at.records, signoff.TUICR, sid)
        if held is None or held.data.get("head") != head:
            sha = tuicr.review_commit(at.path, base, hunks, self._runner) if hunks else None
            selected = [{"path": h.path, "reason": h.reason} for h in hunks]
            data = {"head": head, "base": base, "sha": sha, "hunks": selected}
            held = Record(signoff.TUICR, sid, head, "agent:signoff", data)
            ops.ensure_record(self._store_for_run(), sid, held)
        sha = held.data.get("sha")
        if not sha:
            print(f"done: no hunk of {at.story['identifier']} is selected for review")
            return
        session = tuicr.session_for(at.path, sha, self._runner)
        if session is None:
            note = "run again after tuicr opens to seed the reasons"
        else:
            present = tuicr.comments(at.path, session["slug"], self._runner)
            added = tuicr.seed_comments(session["slug"], hunks, at.path, self._runner, present)
            note = "seeded the missing reasons as comments" if added else "the reasons are seeded"
        print(f"done: {_shown_command(at.path, held.data['base'], sha)}; {note}")
        for hunk in held.data.get("hunks", []):
            print(f"- {hunk['path']}: {hunk['reason']}")


def _last_branch(records: list[Record], story: str) -> dict:
    latest = review.last(review.counted(records, f"{story}:branch")[0])
    return latest.data | {"n": latest.subject.rsplit(":", 1)[1]} if latest else {}


def _open_plan(records: list[Record], story: str) -> list[str]:
    latest = review.last(review.counted(records, f"{story}:plan")[0])
    fixed = review.accepted(records)
    found = (
        [f for f in review.open_findings(latest, review.declined(records)) if f["id"] not in fixed] if latest else []
    )
    if not found:
        return []
    lines = [f"plan round {latest.subject.rsplit(':', 1)[1]}, open:"]
    plan = versions.held(records, implplan.PURPOSE, story)
    if plan is None or review.reviewed(latest) != plan.version:
        lines.append(f"reviewed plan version {review.reviewed(latest)[:12]}, not the current one")
    return lines + _folded(found)


def _folded(found: list[dict]) -> list[str]:
    sure = [f for f in found if f["confidence"] >= review.DOUBTFUL]
    doubtful = [f for f in found if f["confidence"] < review.DOUBTFUL]
    lines = [_finding(f) for f in sure] or (["Nothing found."] if not doubtful else [])
    return lines + (["doubtful:", *(_finding(f) for f in doubtful)] if doubtful else [])


def _shown_command(worktree: Path | None, base: str, sha: str) -> str:
    return f"cd {shlex.quote(str(worktree))} && {shlex.join(tuicr.command(base, sha))}"


def _built(at: Story) -> dict | None:
    held = versions.held(at.records, signoff.TUICR, at.story["id"])
    return held.data if held is not None and held.data.get("head") == at.head else None


def _built_status(comments: list[dict]) -> str:
    if not comments:
        return "built"
    return f"built, {len(comments)} comment{'' if len(comments) == 1 else 's'} from you"


def _tuicr(at: Story, comments: list[dict]) -> list[str]:
    data = _built(at)
    if data is None:
        return ["no review commit is built at this head"]
    if not data.get("sha"):
        return ["no hunk is selected for review"]
    lines = [_shown_command(at.path, data["base"], data["sha"])]
    lines += [f"- {h['path']}: {h['reason']}" for h in data.get("hunks", [])]
    lines += ["your comments:", *(f"- {c.get('location')}: {c.get('content')}" for c in comments)] if comments else []
    return lines


def _what_changed(at: Story) -> list[str]:
    summary = signoff.held_summary(at.records, at.story["id"], at.head)
    if summary is None:
        return ["no change summary is saved at this head"]
    lines: list[str] = []
    for intent in summary.intents:
        lines += [intent.title, *(f"  {line}" for line in intent.lines)]
    lines += [f"new here: {n.text} ({n.path})" for n in summary.new_here]
    return lines or ["the change summary names no intent"]


def _evidence(at: Story) -> list[str]:
    held = versions.held(at.records, CHECKS, at.story["id"])
    if held is None:
        lines = ["checks: not run"]
    elif held.version != at.head:
        lines = [f"checks: last run at {held.version}, not this head"]
    elif held.data.get("passed"):
        lines = ["checks: passed at this head"]
    else:
        lines = [f"checks: {held.data.get('command')} failed", str(held.data.get("tail") or "")]
    summary = signoff.held_summary(at.records, at.story["id"], at.head)
    if summary is not None:
        lines += [f"screenshot: {shot}" for shot in summary.screenshots]
        lines += [f"state diagram: {summary.state_diagram}"] if summary.state_diagram else []
    return lines


def _findings(at: Story) -> list[str]:
    last = _last_branch(at.records, at.story["id"])
    if not last:
        return ["the branch review has not run", *_open_plan(at.records, at.story["id"])]
    lines = [f"branch round {last['n']}"]
    if last.get("head") and last["head"] != at.head:
        lines.append(f"reviewed at {last['head']}, not this head")
    lines += _folded(last.get("findings", []))
    lines += _open_plan(at.records, at.story["id"])
    declined = _declined(at.records)
    lines += ["declined by the person:", *(_finding(f) for f in declined)] if declined else []
    return lines


def _quiz(at: Story) -> list[str]:
    held = quiz.held_set(at.records, at.story["id"])
    if held is None or held.data.get("head") != at.head:
        return ["no quiz is saved at this head"]
    items = quiz.items_of(held)
    left = quiz.open_items(at.records, at.story["id"])
    lines = [f"{len(items)} items; open: {', '.join(map(str, left)) or 'none'}"]
    return lines + [f"{n} {items[n - 1].question}" for n in left]


@register("merge")
class Merge(_Located):
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
        parsed = _parse(args, MERGE_USAGE)
        at = self._located(parsed["words"][0], parsed["team_key"])
        held = signoff.signed(at.records, at.story["id"])
        if held is None:
            print(f"{at.story['identifier']}: the person has not signed off")
            return
        head = signoff.signed_head(held)
        standing = signoff.standing(at.records, at.story["id"], head)
        print(f"{at.story['identifier']}: {standing or f'signed off at {head}, ready to merge pinned to it'}")

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args, MERGE_USAGE)
        at = self._located(parsed["words"][0], parsed["team_key"])
        sid, ref = at.story["id"], at.story["identifier"]
        held = signoff.signed(at.records, sid)
        head = signoff.signed_head(held) if held else ""
        if held is None or not signoff.current(at.records, sid, head):
            reason = signoff.standing(at.records, sid, head) if held else "the person has not signed off"
            raise Refusal(f"{ref} has no current sign-off: {reason}")
        if at.head is not None and at.head != head:
            self._void(at.story, held, f"head moved: the worktree is at {at.head}")
        branch = branch_of(at.records, at.story)
        repo = gh.repo_name(at.clone, self._runner)
        pr = gh.pr_for(branch, repo, self._runner) if branch else None
        if pr is None:
            raise Refusal(f"{ref} has no open pull request")
        if pr["headRefOid"] != head:
            if git.is_ancestor(at.clone, pr["headRefOid"], head, self._runner):
                raise Refusal(f"the pull request is at {pr['headRefOid']}, behind {head}; push the signed head first")
            self._void(at.story, held, f"head moved: the pull request is at {pr['headRefOid']}")
        if pr["isDraft"]:
            gh.mark_ready(pr["number"], repo, self._runner)
        start = self._clock()
        while True:
            found = gh.checks(pr["number"], repo, self._runner)
            if found.state == "fail":
                self._void(at.story, held, "checks failed: " + ", ".join(found.failing))
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
            self._void(at.story, held, "head moved: GitHub refused the merge pinned to the signed head")
        print(f"done: merged {ref} at {head}")

    def _void(self, story: dict, sign: Record, reason: str) -> None:
        ops.ensure_record(self._store_for_run(), story["id"], signoff.void_record(story["id"], sign, reason))
        head = signoff.signed_head(sign)
        raise Refusal(f"the sign-off at {head} is void, {reason}; {story['identifier']} needs a new sign-off")
