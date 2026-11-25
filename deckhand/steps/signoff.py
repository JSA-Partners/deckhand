from __future__ import annotations

import json
import shlex
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from deckhand import answers, gh, git, implplan, inputs, ops, review, signoff, tuicr, versions, wakes
from deckhand.checks import KIND as CHECKS
from deckhand.checks import passing
from deckhand.records import Record
from deckhand.step import Refusal, hand_role, register, team_key
from deckhand.steps.build import DEFAULT_BASE, Located, StoryStep, branch_of
from deckhand.store import Store
from deckhand.summary import SHAPE, ChangeSummary, around, location, parse_summary

USAGE = (
    "signoff takes REF and [--team-key KEY] on both verbs; context [--screen 1-5] [--json],"
    " apply summary --summary SUMMARY.json or apply review [--watch]"
)
SCREENS = ("what changed", "evidence", "findings", "what to check", "tuicr")
ACTIONS = ("summary", "review")
PLAN_FILE = "PLAN.md"
WATCH_POLL = 2
OPEN_WAIT = 3600
REVIEW_WAIT = 3600


def _parse(args: list[str], usage: str) -> dict:
    found: dict = {"words": [], "json": False, "watch": False}
    rest = iter(args)
    for arg in rest:
        if arg in ("--json", "--watch"):
            found[arg[2:]] = True
        elif arg in ("--team-key", "--summary", "--screen"):
            found[arg[2:].replace("-", "_")] = next(rest, "")
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {usage}")
        else:
            found["words"].append(arg)
    if not found["words"]:
        raise Refusal(f"a story REF is required; {usage}")
    return found


def _read(path: str) -> str:
    return inputs.read(path, "--summary", USAGE)


def _declined(records: list[Record]) -> list[dict]:
    ids = review.declined(records)
    found: dict[str, dict] = {}
    for record in records:
        if record.kind == review.KIND:
            found |= {item["id"]: item for item in record.data.get("findings", []) if item["id"] in ids}
    return list(found.values())


def _finding(item: dict) -> str:
    return f"- {item['severity']} {item['id']} {item['lens']} {item['path']}:{item['line']} {item['summary']}"


@register("signoff")
class Signoff(StoryStep):
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
        parsed = _parse(args, USAGE)
        at = _from_records(self.locate(parsed["words"][0], team_key(parsed)))
        screen = parsed.get("screen", "")
        if screen and screen not in ("1", "2", "3", "4", "5"):
            raise Refusal(f"--screen takes 1 to 5; {USAGE}")
        facts = self._screen(at, int(screen)) if screen else self._overview(at)
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        for line in facts["lines"]:
            print(line)

    def _overview(self, at: Located) -> dict:
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
            "what to check": f"{len(summary.to_check)} lines" if summary else "no change summary at this head",
            "tuicr": self._pr_diff(at) if at.path is None else _tuicr_status(built, comments),
        }
        found_gaps = signoff.gaps(at.records, sid, head, at.story["identifier"])
        missing = [gap.hand if hand_role() else gap.person for gap in found_gaps]
        standing = signoff.standing(at.records, sid, head, at.policy.second_signoff)
        missing += [standing] if standing and standing not in missing else []
        lines = ["missing:", *(f"- {m}" for m in missing)] if missing else []
        lines += [f"sign-off for {at.story['identifier']} at {head}"]
        lines += [f"{n} {name}: {status[name]}" for n, name in enumerate(SCREENS, start=1)]
        lines += [] if missing else ["signed off at this head"]
        found = {"story": at.story["identifier"], "head": head, "screens": status, "missing": missing}
        if hand_role():
            found["unfixed"] = review.unfixed(at.records)
            lines += wakes.unfixed_lines(at.records)
            shape = ["change summary, a JSON object:", *(f"- {name}: {meaning}" for name, meaning in SHAPE.items())]
            lines += [] if summary else shape
        return found | {"comments": comments, "lines": lines}

    def _screen(self, at: Located, n: int) -> dict:
        if at.head is None:
            return {"screen": n, "lines": [f"{at.story['identifier']} has no worktree on this machine"]}
        lines = [f"{n} {SCREENS[n - 1]} for {at.story['identifier']} at {at.head}"]
        if n == 5 and at.path is None:
            return {"screen": n, "lines": [*lines, self._pr_diff(at)]}
        if n == 5:
            comments = self._comments(at)
            return {"screen": n, "comments": comments, "lines": lines + _tuicr(at, comments)}
        lines += {1: _what_changed, 2: _evidence, 3: _findings, 4: self._to_check}[n](at)
        return {"screen": n, "lines": lines}

    def _to_check(self, at: Located) -> list[str]:
        summary = signoff.held_summary(at.records, at.story["id"], at.head)
        if summary is None or at.head is None:
            return ["no change summary is saved at this head"]
        lines = []
        for entry in summary.to_check:
            path, line = location(entry.where)
            try:
                code = around(git.show(at.path or at.clone, at.head, path, self._runner), line)
            except git.GitError:
                code = [f"{path} cannot be read at {at.head}"]
            lines += [f"{entry.where}: {entry.why}", *code]
        return lines

    def _pr_diff(self, at: Located) -> str:
        branch = branch_of(at.records, at.story)
        try:
            repo = gh.repo_name(at.clone, self._runner)
            pr = gh.pr_for(branch, repo, self._runner) if branch else None
        except gh.GhError:
            return "the pull request cannot be read from here; run gh pr diff in a terminal"
        if pr is None:
            return f"{at.story['identifier']} has no open pull request"
        return f"run gh pr diff {pr['number']} --repo {repo} in a terminal"

    def _comments(self, at: Located) -> list[dict]:
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
        at = self.locate(parsed["words"][0], team_key(parsed))
        if at.path is None or at.head is None:
            ref = at.story["identifier"]
            raise Refusal(f"{ref} has no worktree; run build apply {ref} worktree")
        if parsed["watch"] and parsed["words"][1] != "review":
            raise Refusal(f"--watch applies only to apply review; {USAGE}")
        if parsed["words"][1] == "summary":
            self._summary(at, parsed)
            return
        self._review(at, parsed["watch"])

    def _summary(self, at: Located, parsed: dict) -> None:
        self.owned(at)
        summary = parse_summary(_read(parsed.get("summary", "")))
        if git.dirty(at.path, self._runner):
            raise Refusal(f"{at.path} has uncommitted changes; commit them first so the summary is pinned")
        hunks = tuicr.parse_hunks(
            git.diff(at.path, git.merge_base(at.path, "HEAD", DEFAULT_BASE, self._runner), runner=self._runner)
        )
        for index, entry in enumerate(summary.to_check):
            if not tuicr.holds(hunks, entry.where):
                shown = tuicr.describe_ranges(tuicr.shown_ranges(hunks)) or "no line"
                raise Refusal(
                    f"to_check entry {index} names {entry.where}, which the diff does not show; it shows {shown}"
                )
        record = signoff.summary_record(at.story["id"], at.head, summary)
        ops.ensure_record(self._store_for_run(), at.story["id"], record)
        print(f"done: saved the change summary for {at.story['identifier']} at {at.head}")

    def _review(self, at: Located, watch: bool) -> None:
        sid, head = at.story["id"], at.head
        summary = signoff.held_summary(at.records, sid, head)
        plan = versions.held(at.records, implplan.PURPOSE, sid)
        if summary is None or plan is None:
            raise Refusal(f"{at.story['identifier']} needs its plan and a change summary at {head} first")
        base, hunks = review_hunks(at, at.path, summary, plan.data, self._runner)
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
        command = _shown_command(at.path, held.data["base"], sha)
        if watch:
            print(f"done: {command}", *_reasons(held.data), sep="\n", flush=True)
            self._watch(at, sha, hunks, held.data)
            return
        session = tuicr.session_for(at.path, sha, self._runner)
        note = "--watch seeds the reasons once tuicr opens"
        if session is not None:
            note = self._seed(at, session, hunks)
        print(f"done: {command}; {note}")
        for line in _reasons(held.data):
            print(line)

    def _seed(self, at: Located, session: dict, hunks: list[tuicr.Hunk]) -> str:
        present = tuicr.comments(at.path, session["slug"], self._runner)
        added = tuicr.seed_comments(session["slug"], hunks, at.path, self._runner, present)
        return "seeded the missing reasons as comments" if added else "the reasons are seeded"

    def _watch(self, at: Located, sha: str, hunks: list[tuicr.Hunk], built: dict) -> None:
        again = f"run signoff apply {at.story['identifier']} review --watch again"
        print("waiting for tuicr to open", flush=True)
        session = self._until(at, sha, True, OPEN_WAIT)
        if session is None:
            print(f"tuicr did not open within {OPEN_WAIT // 60} minutes; {again}")
            return
        print(f"tuicr is open; {self._seed(at, session, hunks)}", flush=True)
        if self._until(at, sha, False, REVIEW_WAIT) is None:
            print(f"tuicr is still open after {REVIEW_WAIT // 60} minutes; {again}")
            return
        found = tuicr.persons(tuicr.comments(at.path, session["slug"], self._runner), built.get("hunks", []))
        if not found:
            print("tuicr closed with no comment from you")
            return
        print("tuicr closed; your comments:")
        for comment in found:
            print(f"- {comment.get('location')}: {comment.get('content')}")

    def _until(self, at: Located, sha: str, active: bool, wait: int) -> dict | None:
        start = self._clock()
        while True:
            session = tuicr.session_for(at.path, sha, self._runner) or {}
            if bool(session.get("active")) == active:
                return session
            if self._clock() - start >= wait:
                return None
            self._sleep(WATCH_POLL)


def review_hunks(
    at: Located, path: Path, summary: ChangeSummary | None, manifest: dict, runner: git.Runner
) -> tuple[str, list[tuicr.Hunk]]:
    base = git.merge_base(path, "HEAD", DEFAULT_BASE, runner)
    findings = _last_branch(at.records, at.story["id"]).get("findings", [])
    doubtful = {f["path"] for f in findings if f["confidence"] < review.DOUBTFUL}
    diff = git.diff(path, base, runner=runner)
    return base, tuicr.select_hunks(diff, at.policy, doubtful, manifest, summary)


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
    return lines + _folded([_manifest_path(f) for f in found])


def _manifest_path(item: dict) -> dict:
    local = str(answers.state_dir() / review.TEXTS)
    return item | {"path": PLAN_FILE} if str(item["path"]).startswith(local) else item


def _folded(found: list[dict]) -> list[str]:
    sure = [f for f in found if f["confidence"] >= review.DOUBTFUL]
    doubtful = [f for f in found if f["confidence"] < review.DOUBTFUL]
    lines = [_finding(f) for f in sure] or (["Nothing found."] if not doubtful else [])
    return lines + (["doubtful:", *(_finding(f) for f in doubtful)] if doubtful else [])


def _shown_command(worktree: Path | None, base: str, sha: str) -> str:
    return f"cd {shlex.quote(str(worktree))} && {shlex.join(tuicr.command(base, sha))}"


def _built(at: Located) -> dict | None:
    held = versions.held(at.records, signoff.TUICR, at.story["id"])
    return held.data if held is not None and held.data.get("head") == at.head else None


def _from_records(at: Located) -> Located:
    if at.head is not None:
        return at
    held = versions.held(at.records, signoff.SUMMARY, at.story["id"])
    head = held.data.get("head") if held is not None else None
    return replace(at, head=head) if isinstance(head, str) and head else at


def _tuicr_status(built: dict | None, comments: list[dict]) -> str:
    if built is None:
        return "not built at this head"
    if not comments:
        return "built"
    return f"built, {len(comments)} comment{'' if len(comments) == 1 else 's'} from you"


def _reasons(built: dict) -> list[str]:
    pairs = [(str(h["path"]), str(h["reason"])) for h in built.get("hunks", [])]
    return [f"- {path}: {reason}" for path, reason in tuicr.file_reasons(pairs).items()]


def _tuicr(at: Located, comments: list[dict]) -> list[str]:
    data = _built(at)
    if data is None:
        return ["no review commit is built at this head"]
    if not data.get("sha"):
        return ["no hunk is selected for review"]
    lines = [_shown_command(at.path, data["base"], data["sha"])]
    lines += _reasons(data)
    lines += ["your comments:", *(f"- {c.get('location')}: {c.get('content')}" for c in comments)] if comments else []
    return lines


def _what_changed(at: Located) -> list[str]:
    summary = signoff.held_summary(at.records, at.story["id"], at.head)
    if summary is None:
        return ["no change summary is saved at this head"]
    lines: list[str] = []
    for intent in summary.intents:
        lines += [intent.title, *(f"  {line}" for line in intent.lines)]
    lines += [f"new here: {n.text} ({n.path})" for n in summary.new_here]
    return lines or ["the change summary names no intent"]


def _evidence(at: Located) -> list[str]:
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


def _findings(at: Located) -> list[str]:
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
