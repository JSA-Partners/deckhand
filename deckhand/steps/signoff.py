from __future__ import annotations

import json
import shlex
from dataclasses import replace
from pathlib import Path

from deckhand import answers, gh, git, implplan, inputs, ops, quiz, review, signoff, tuicr, versions
from deckhand.checks import KIND as CHECKS
from deckhand.checks import passing
from deckhand.records import Record
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import DEFAULT_BASE, Located, StoryStep, branch_of
from deckhand.summary import parse_summary

USAGE = (
    "signoff takes REF and [--team-key KEY] on both verbs; context [--screen 1-5] [--json],"
    " apply summary --summary SUMMARY.json or apply review"
)
SCREENS = ("what changed", "evidence", "findings", "tuicr", "quiz")
ACTIONS = ("summary", "review")
PLAN_FILE = "PLAN.md"


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
            "tuicr": self._pr_diff(at) if at.path is None else _tuicr_status(built, comments),
            "quiz": "passed" if quiz.passed(at.records, sid, head) else "not passed",
        }
        missing = signoff.gaps(at.records, sid, head)
        standing = signoff.standing(at.records, sid, head, at.policy.second_signoff)
        missing += [standing] if standing else []
        lines = [f"sign-off for {at.story['identifier']} at {head}"]
        lines += [f"{n} {name}: {status[name]}" for n, name in enumerate(SCREENS, start=1)]
        lines += ["missing:", *(f"- {m}" for m in missing)] if missing else ["signed off at this head"]
        found = {"story": at.story["identifier"], "head": head, "screens": status, "missing": missing}
        return found | {"comments": comments, "lines": lines}

    def _screen(self, at: Located, n: int) -> dict:
        if at.head is None:
            return {"screen": n, "lines": [f"{at.story['identifier']} has no worktree on this machine"]}
        lines = [f"{n} {SCREENS[n - 1]} for {at.story['identifier']} at {at.head}"]
        if n == 4 and at.path is None:
            return {"screen": n, "lines": [*lines, self._pr_diff(at)]}
        if n == 4:
            comments = self._comments(at)
            return {"screen": n, "comments": comments, "lines": lines + _tuicr(at, comments)}
        lines += {1: _what_changed, 2: _evidence, 3: _findings, 5: _quiz}[n](at)
        return {"screen": n, "lines": lines}

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
        if parsed["words"][1] == "summary":
            self._summary(at, parsed)
            return
        self._review(at)

    def _summary(self, at: Located, parsed: dict) -> None:
        self.owned(at)
        summary = parse_summary(_read(parsed.get("summary", "")))
        if git.dirty(at.path, self._runner):
            raise Refusal(f"{at.path} has uncommitted changes; commit them first so the summary is pinned")
        record = signoff.summary_record(at.story["id"], at.head, summary)
        ops.ensure_record(self._store_for_run(), at.story["id"], record)
        print(f"done: saved the change summary for {at.story['identifier']} at {at.head}")

    def _review(self, at: Located) -> None:
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
        for line in _reasons(held.data):
            print(line)


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
    held = quiz.held_set(at.records, at.story["id"])
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


def _quiz(at: Located) -> list[str]:
    held = quiz.held_set(at.records, at.story["id"])
    if held is None or held.data.get("head") != at.head:
        return ["no quiz is saved at this head"]
    items = quiz.items_of(held)
    left = quiz.open_items(at.records, at.story["id"])
    lines = [f"{len(items)} items; open: {', '.join(map(str, left)) or 'none'}"]
    return lines + [f"{n} {items[n - 1].question}" for n in left]
