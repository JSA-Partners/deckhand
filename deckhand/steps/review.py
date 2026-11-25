from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from deckhand import answers, asks, features, git, implplan, inputs, lenses, ops, review, spec, versions
from deckhand.checks import passing
from deckhand.policy import STAGES, Policy
from deckhand.records import Record, content_hash
from deckhand.step import Refusal, register, team_key
from deckhand.steps.build import Located, StoryStep
from deckhand.store import Store

USAGE = (
    "review takes REF, --stage plan|branch and [--team-key KEY], feature-review [--team-key KEY] alone; context takes"
    " --json; apply takes round --findings FINDINGS.json --target HASH (the target_hash context printed),"
    " round --target HASH alone when context stops at carry, or resolve with --accept ID, --propose-accept ID or"
    " --propose-decline ID (repeat each), and one --reason TEXT that covers every proposal in the call"
)
PROPOSING = {"--propose-accept": review.ACCEPT, "--propose-decline": review.DECLINE}
FEATURE = "feature"
ACTIONS = ("round", "resolve")
STOPS = {
    "unchanged": "the target is unchanged since the last round; nothing to review",
    "clean": "the last round holds no accepted P1; the review is done",
    "cap": "three rounds left a P1 open; the person decides whether to proceed or rework",
    "proceeded": "the person chose Proceed at the cap and no other P1 is open; the review is done",
    "checks": "checks have not passed at this commit; run them first",
    "carry": "the story's own change is unchanged at a new head; record apply round --target HASH with no"
    " --findings, which carries the last round forward without reviewers",
}


def _parse(args: list[str]) -> dict:
    found: dict = {"words": [], "accept": [], "decline": [], "propose": [], "json": False}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg == "--reason" and "reason" in found:
            raise Refusal(
                "one --reason covers every proposal in the call; put proposals with different reasons in separate calls"
            )
        elif arg in ("--team-key", "--stage", "--findings", "--target", "--reason"):
            found[arg[2:].replace("-", "_")] = next(rest, "")
        elif arg in ("--accept", "--decline"):
            found[arg[2:]].append(next(rest, ""))
        elif arg in PROPOSING:
            found["propose"].append((next(rest, ""), PROPOSING[arg]))
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    return found


@dataclass
class Subject:
    prefix: str
    stage: str
    label: str
    stories: list[str]
    records: list[Record]
    story: dict | None = None
    feature: features.Feature | None = None
    located: Located | None = None


@dataclass
class Target:
    hash: str
    text: str | None = None
    head: str = ""
    note: str = ""
    base: str = ""


def _status(item: dict, declined: set[str], waived: set[str], accepted: set[str]) -> str:
    if item["id"] in declined:
        return "declined by the person"
    if item["id"] in waived:
        return "waived by the person"
    return "accepted" if item["id"] in accepted else "open"


@register("review")
class Review(StoryStep):
    stage_fixed: str | None = None

    def _subject(self, parsed: dict) -> Subject:
        if self.stage_fixed != FEATURE:
            return self._story_subject(parsed)
        feature = features.open_feature(self._store_for_run(), team_key(parsed))
        if feature is None:
            raise Refusal("no feature is open")
        current = features.phase(feature)
        if parsed["words"]:
            raise Refusal(f"feature-review takes no story REF; {USAGE}")
        if not features.replanning(feature):
            raise Refusal(f"{features.describe(feature.project)} is {current.value}; the feature review is over")
        if not feature.live:
            raise Refusal(f"{features.describe(feature.project)} has no stories to review")
        held = [r for s in feature.live for r in feature.records.get(s["id"], [])]
        project = feature.project["id"]
        ids = [s["id"] for s in feature.live]
        return Subject(f"{project}:{FEATURE}", FEATURE, feature.project["name"], ids, held, None, feature)

    def _story_subject(self, parsed: dict) -> Subject:
        stage = parsed.get("stage", "")
        if stage not in STAGES:
            raise Refusal(f"--stage must be plan or branch; {USAGE}")
        if len(parsed["words"]) != 1:
            raise Refusal(f"review takes one story REF; {USAGE}")
        at = self.locate(parsed["words"][0], team_key(parsed))
        story = at.story
        return Subject(
            f"{story['id']}:{stage}", stage, story["identifier"], [story["id"]], at.records, story, at.feature, at
        )

    def _lenses(self, subject: Subject, repo: Path) -> list[str]:
        if subject.stage == FEATURE:
            return ["contract"]
        policy = Policy.load(repo)
        level = versions.tier(subject.records, subject.stories[0])
        if level is None:
            raise Refusal(f"{subject.label} has no tier in the story map")
        return lenses.lenses_for(subject.stage, level, policy)

    def _target(self, store: Store, subject: Subject, repo: Path) -> Target:
        state = answers.state_dir()
        if subject.stage == FEATURE:
            text = features.review_text(store, subject.feature)
            if text is None:
                raise Refusal("the Contract section is not saved")
            found = Target(content_hash(text), text=spec.plain(text))
        elif subject.stage == "plan":
            plan = versions.held(subject.records, implplan.PURPOSE, subject.stories[0])
            if plan is None:
                raise Refusal(f"{subject.label} has no implementation plan saved")
            text = review.saved_text(state, subject.stories[0], plan.version)
            if text is None:
                document = store.document(ops.create_id(subject.stories[0], implplan.PURPOSE))
                body = document["body"] if document else ""
                if implplan.version(body.encode(), plan.data) == plan.version:
                    text = body
            found = Target(plan.version, text=text)
        else:
            base = git.merge_base(repo, "HEAD", "origin/HEAD", self._runner)
            diff = git.diff(repo, base, runner=self._runner)
            found = Target(content_hash(diff), text=diff, head=git.head(repo, self._runner), base=base)
            found.note = f"the diff to review is git diff {base}..HEAD in {repo}"
        if subject.stage == "plan" and found.text is None:
            found.note = f"the plan text is not on this machine; read the Implementation plan on {subject.label}"
        return found

    def _scoped_diff(self, subject: Subject, target: Target, last: Record, repo: Path) -> tuple[str | None, str]:
        if subject.stage == "branch":
            if not last.data.get("head"):
                return None, "the last branch round holds no head to scope the diff from; review the whole diff"
            try:
                return git.changed_since(repo, last.data["head"], target.base, self._runner), ""
            except git.GitError:
                return None, "the last reviewed commit is gone or does not merge with the base; review the whole diff"
        old = review.saved_text(answers.state_dir(), subject.prefix.split(":")[0], last.data["target_hash"])
        if old is None or target.text is None:
            return None, "the previously reviewed text is not on this machine; review the whole text"
        return review.text_diff(old, target.text), ""

    def _due(
        self, subject: Subject, target: Target, repo: Path
    ) -> tuple[review.RoundPlan | review.Stop, str | None, str]:
        records, prefix = subject.records, subject.prefix
        counted, _ = review.counted(records, prefix)
        excused, since = review.excused(records, prefix), review.proceeded(records, prefix)
        plan = review.next_round(counted, target.hash, excused, target.head, since)
        last = review.last(review.rounds_of(records, prefix))
        if isinstance(plan, review.Stop) or plan.carry or not plan.scoped or last is None:
            return plan, None, ""
        diff, note = self._scoped_diff(subject, target, last, repo)
        return review.carry_when_empty(plan, diff if subject.stage == "branch" else None), diff, note

    def _clone_of(self, subject: Subject) -> Path:
        if subject.located is not None:
            return subject.located.clone
        return self._repo or Path.cwd()

    def _worktree(self, subject: Subject, clone: Path) -> Path:
        if subject.stage != "branch":
            return clone
        at = subject.located
        if at is None or at.path is None:
            raise Refusal(f"{subject.label} has no worktree; run build apply {subject.label} worktree")
        return at.path

    def _unchecked(self, subject: Subject, repo: Path) -> bool:
        head = git.head(repo, self._runner) if subject.stage == "branch" else ""
        return bool(head) and not passing(subject.records, subject.stories[0], head)

    def _checked(self, subject: Subject, repo: Path) -> None:
        if self._unchecked(subject, repo):
            raise Refusal(f"{subject.label} has no passing checks at this commit; run checks apply {subject.label}")

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        subject = self._subject(parsed)
        store, clone = self._store_for_run(), self._clone_of(subject)
        repo = self._worktree(subject, clone)
        counted, cut = review.counted(subject.records, subject.prefix)
        declined, accepted = review.declined(subject.records), review.accepted(subject.records)
        excused = review.excused(subject.records, subject.prefix)
        last = review.last(review.rounds_of(subject.records, subject.prefix))
        target = self._target(store, subject, repo)
        checked = not self._unchecked(subject, repo)
        plan, diff, note = self._due(subject, target, repo) if checked else (review.Stop("checks"), None, "")
        facts: dict = {"subject": subject.label, "stage": subject.stage, "target_hash": target.hash}
        facts |= {"stop": None, "round": None, "scoped": False, "lenses": [], "target": None, "diff": None}
        facts |= {"note": target.note, "spec": None}
        if isinstance(plan, review.Stop):
            held = review.last(counted)
            proceeded = held is not None and bool(review.open_p1(held, declined)) and plan.reason == "clean"
            facts["stop"] = "proceeded" if proceeded else plan.reason
        elif plan.carry:
            facts |= {"stop": "carry", "round": plan.n + cut}
        else:
            plan = replace(plan, n=plan.n + cut)
            facts |= {"round": plan.n, "scoped": plan.scoped}
            names = self._lenses(subject, clone)
            facts["lenses"] = [{"name": n, "path": str(lenses.path_of(n, clone))} for n in names]
            key, text = target.hash, target.text
            facts["note"] = note or facts["note"]
            if subject.stage != "branch":
                facts["diff"] = diff
            elif diff is not None:
                key, text = content_hash(diff), diff
            owner = subject.prefix.split(":")[0]
            if text is not None:
                facts["target"] = str(review.save_text(answers.state_dir(), owner, key, text))
            if subject.stage != FEATURE:
                approved = spec.text(spec.gather(store, subject.feature, subject.story))
                name = "spec-" + content_hash(approved)[:12]
                facts["spec"] = str(review.save_text(answers.state_dir(), owner, name, approved))
        facts["carried"] = []
        if isinstance(plan, review.RoundPlan) and plan.scoped and not plan.carry:
            facts["carried"] = [
                {k: f[k] for k in ("id", "lens", "path", "line", "summary")}
                for f in review.carried(subject.records, subject.prefix)
            ]
        facts["last_round"] = None
        if last is not None:
            waived = excused - declined
            items = [item | {"status": _status(item, declined, waived, accepted)} for item in last.data["findings"]]
            facts["last_round"] = {"n": review.round_number(last), "findings": items}
        if parsed["json"]:
            print(json.dumps(facts, indent=2))
            return
        _print(facts)

    def apply(self, args: list[str]) -> None:
        parsed = _parse(args)
        words = parsed["words"]
        action = words.pop(1 if self.stage_fixed is None and len(words) > 1 else 0) if words else ""
        if action not in ACTIONS:
            raise Refusal(f"apply takes round or resolve; {USAGE}")
        if action == "resolve":
            self._resolve(parsed)
            return
        if not parsed.get("target"):
            raise Refusal(f"--target is required; {USAGE}")
        subject = self._writable(parsed)
        store = self._store_for_run()
        repo = self._worktree(subject, self._clone_of(subject))
        self._checked(subject, repo)
        counted, cut = review.counted(subject.records, subject.prefix)
        target = self._target(store, subject, repo)
        if target.hash != parsed["target"]:
            raise Refusal(f"the target changed since context; run context again and review {target.hash}")
        plan, _, _ = self._due(subject, target, repo)
        if isinstance(plan, review.Stop):
            raise Refusal(f"no review round is due: {plan.reason}, {STOPS[plan.reason]}")
        if plan.carry and parsed.get("findings"):
            raise Refusal(
                f"the story's own change is unchanged at {target.head}, so this round carries the last round forward;"
                " run apply round --target HASH without --findings"
            )
        found = [] if plan.carry else review.parse_findings(_read(parsed.get("findings", "")))
        plan, owner = replace(plan, n=plan.n + cut), subject.prefix.split(":")[0]
        if plan.carry:
            record = review.carry_record(owner, subject.stage, plan, review.last(counted), target.head, target.hash)
        else:
            carried = [f["id"] for f in review.carried(subject.records, subject.prefix)] if plan.scoped else []
            ordered = review.ordered(found)
            record = review.round_record(owner, subject.stage, plan, target.hash, ordered, target.head, carried)
        for story in subject.stories:
            ops.ensure_record(store, story, record)
        held = record.data["findings"]
        p1 = sum(f["severity"] == "P1" for f in held)
        what = "carried the last round forward as" if plan.carry else "recorded"
        print(f"done: {what} round {plan.n} of {subject.label} {subject.stage}, {len(held)} findings, {p1} P1")

    def _writable(self, parsed: dict) -> Subject:
        subject = self._subject(parsed)
        if subject.located is not None:
            self.owned(subject.located)
        return subject

    def _resolve(self, parsed: dict) -> None:
        subject = self._writable(parsed)
        if parsed["decline"]:
            ids = " ".join(f"--propose-decline {id}" for id in parsed["decline"])
            raise Refusal(f"only the person declines a finding; propose it with {ids} --reason TEXT")
        if not parsed["accept"] and not parsed["propose"]:
            raise Refusal(f"resolve takes --accept ID, --propose-accept ID or --propose-decline ID; {USAGE}")
        if parsed["propose"] and not parsed.get("reason", "").strip():
            raise Refusal(f"a proposal needs --reason TEXT; {USAGE}")
        reason, where = parsed.get("reason", "").strip(), f"{subject.label} {subject.stage}"
        found = asks.resolutions(subject.records, subject.prefix, parsed["accept"], parsed["propose"], reason, where)
        store = self._store_for_run()
        for record in found:
            for story in subject.stories:
                ops.ensure_record(store, story, record)
        print(
            f"done: accepted {len(parsed['accept'])} and proposed {len(parsed['propose'])} findings"
            f" on {subject.label} {subject.stage}"
        )


@register("feature-review")
class FeatureReview(Review):
    stage_fixed = FEATURE


def _read(path: str) -> str:
    return inputs.read(path, "--findings", USAGE)


def _line(item: dict) -> str:
    flag = ", changes delivery" if item.get("changes_delivery") else ""
    return f"- {item['severity']} {item['id']} {item['lens']} {item['path']}:{item['line']} {item['summary']}" + (
        f" ({item['status']}{flag})"
    )


def _print(facts: dict) -> None:
    head = f"review {facts['subject']} {facts['stage']}"
    if facts["stop"]:
        print(f"{head}: stop, {facts['stop']}: {STOPS[facts['stop']]}")
        print(f"target_hash: {facts['target_hash']}")
    else:
        scope = "scoped to the revision" if facts["scoped"] else "the whole target"
        print(f"{head}: round {facts['round']}, {scope}")
        print(f"target_hash: {facts['target_hash']}")
        print("lenses:")
        for lens in facts["lenses"]:
            print(f"- {lens['name']}: {lens['path']}")
        if facts["target"]:
            print(f"target: {facts['target']}")
        if facts["spec"]:
            print(f"spec: {facts['spec']}")
    if facts["carried"]:
        print("open P1s and accepted findings not yet fixed; re-report each the diff does not fix, with the same")
        print("lens, path, line and summary so the id is kept:")
        for item in facts["carried"]:
            print(f"- {item['id']} {item['lens']} {item['path']}:{item['line']} {item['summary']}")
    if facts["note"]:
        print(f"note: {facts['note']}")
    if facts["diff"] is not None:
        print("diff:")
        print(facts["diff"], end="" if facts["diff"].endswith("\n") else "\n")
    last = facts["last_round"]
    if last is None:
        return
    print(f"last round {last['n']}:")
    sure = [f for f in last["findings"] if f["confidence"] >= review.DOUBTFUL]
    doubtful = [f for f in last["findings"] if f["confidence"] < review.DOUBTFUL]
    if not last["findings"]:
        print("Nothing found.")
    for item in sure:
        print(_line(item))
    if doubtful:
        print("doubtful:")
        for item in doubtful:
            print(_line(item))
