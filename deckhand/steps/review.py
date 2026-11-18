from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from deckhand import answers, features, git, implplan, lenses, ops, planning, review, tiers, versions
from deckhand.credentials import Credentials
from deckhand.features import Phase
from deckhand.linear.connect import connect
from deckhand.policy import STAGES, Policy
from deckhand.records import Record, content_hash
from deckhand.step import Refusal, register
from deckhand.store import Store
from deckhand.store.linear import LinearStore

USAGE = (
    "review takes REF, --stage plan|branch and --team-key KEY, feature-review --team-key KEY alone; context takes"
    " --json; apply takes round --findings FINDINGS.json --target HASH (the target_hash context printed),"
    " or resolve --accept ID (repeat for each)"
)
FEATURE = "feature"
ACTIONS = ("round", "resolve")
STOPS = {
    "unchanged": "the target is unchanged since the last round; nothing to review",
    "clean": "the last round holds no accepted P1; the review is done",
    "cap": "three rounds ran and a P1 is still open; the person decides each open P1",
}


def _config_dir() -> Path:
    return Path(os.environ.get("DECKHAND_CONFIG_DIR") or Path.home() / ".config" / "deckhand")


def _parse(args: list[str]) -> dict:
    found: dict = {"words": [], "accept": [], "decline": [], "json": False}
    rest = iter(args)
    for arg in rest:
        if arg == "--json":
            found["json"] = True
        elif arg in ("--team-key", "--stage", "--findings", "--target"):
            found[arg[2:]] = next(rest, "")
        elif arg in ("--accept", "--decline"):
            found[arg[2:]].append(next(rest, ""))
        elif arg.startswith("--"):
            raise Refusal(f"unknown argument {arg}; {USAGE}")
        else:
            found["words"].append(arg)
    if not found.get("team-key"):
        raise Refusal(f"--team-key is required; {USAGE}")
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


@dataclass
class Target:
    hash: str
    text: str | None = None
    head: str = ""
    note: str = ""


def _status(item: dict, declined: set[str], accepted: set[str]) -> str:
    if item["id"] in declined:
        return "declined by the person"
    return "accepted" if item["id"] in accepted else "open"


@register("review")
class Review:
    stage_fixed: str | None = None

    def __init__(self, store: Store | None = None, runner: git.Runner = git.run, repo: Path | None = None) -> None:
        self._store = store
        self._runner = runner
        self._repo = repo

    def _store_for_run(self) -> Store:
        if self._store is not None:
            return self._store
        return LinearStore(connect(Credentials.load(_config_dir()), "app"))

    def _subject(self, parsed: dict) -> Subject:
        store = self._store_for_run()
        feature = features.open_feature(store, parsed["team-key"])
        if feature is None:
            raise Refusal("no feature is open")
        current = features.phase(feature)
        if self.stage_fixed == FEATURE:
            if parsed["words"]:
                raise Refusal(f"feature-review takes no story REF; {USAGE}")
            if current is not Phase.PLANNING:
                raise Refusal(f"{features.describe(feature.project)} is {current.value}; the feature review is over")
            if not feature.live:
                raise Refusal(f"{features.describe(feature.project)} has no stories to review")
            held = [r for s in feature.live for r in feature.records.get(s["id"], [])]
            project = feature.project["id"]
            ids = [s["id"] for s in feature.live]
            return Subject(f"{project}:{FEATURE}", FEATURE, feature.project["name"], ids, held, None, feature)
        stage = parsed.get("stage", "")
        if stage not in STAGES:
            raise Refusal(f"--stage must be plan or branch; {USAGE}")
        if len(parsed["words"]) != 1:
            raise Refusal(f"review takes one story REF; {USAGE}")
        if current is not Phase.BUILDING:
            raise Refusal(f"{features.describe(feature.project)} is {current.value}, not building; approve it first")
        story = features.story(store, feature, parsed["words"][0])
        held = feature.records.get(story["id"], [])
        return Subject(f"{story['id']}:{stage}", stage, story["identifier"], [story["id"]], held, story, feature)

    def _lenses(self, subject: Subject, repo: Path) -> list[str]:
        if subject.stage == FEATURE:
            return ["contract"]
        policy = Policy.load(repo)
        tier = versions.held(subject.records, "tier", subject.stories[0])
        level = tiers.from_record(tier.data) if tier else None
        if level is None:
            raise Refusal(f"{subject.label} has no tier decided")
        return lenses.lenses_for(subject.stage, level, policy)

    def _target(self, store: Store, subject: Subject, repo: Path) -> Target:
        state = answers.state_dir()
        if subject.stage == FEATURE:
            feature = subject.feature
            contract = planning.section(store, feature, "Contract")
            if contract is None:
                raise Refusal("the Contract section is not saved")
            parts = ["# Contract", contract]
            for story in sorted(feature.live, key=lambda s: s["id"]):
                parts += [f"# {story['identifier']} {story['title']}", story["description"] or ""]
            text = "\n\n".join(parts) + "\n"
            found = Target(content_hash(text), text=text)
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
            found = Target(content_hash(diff), text=diff, head=git.head(repo, self._runner))
            found.note = f"the diff to review is git diff {base}..HEAD in {repo}"
        if subject.stage == "plan" and found.text is None:
            found.note = f"the plan text is not on this machine; read the Implementation plan on {subject.label}"
        return found

    def _scoped_diff(self, subject: Subject, target: Target, last: Record, repo: Path) -> tuple[str | None, str]:
        if subject.stage == "branch":
            if not last.data.get("head"):
                raise Refusal(f"the last branch round of {subject.label} holds no head to scope the diff from")
            try:
                return git.diff_since(repo, last.data["head"], self._runner), ""
            except git.GitError:
                return None, "the last reviewed commit is gone; review the whole diff"
        old = review.saved_text(answers.state_dir(), subject.prefix.split(":")[0], last.data["target_hash"])
        if old is None or target.text is None:
            return None, "the previously reviewed text is not on this machine; review the whole text"
        return review.text_diff(old, target.text), ""

    def context(self, args: list[str]) -> None:
        parsed = _parse(args)
        subject = self._subject(parsed)
        store, repo = self._store_for_run(), self._repo or Path.cwd()
        rounds = review.rounds_of(subject.records, subject.prefix)
        declined, accepted = review.declined(subject.records), review.accepted(subject.records)
        last = review.last(rounds)
        target = self._target(store, subject, repo)
        plan = review.next_round(rounds, target.hash, declined)
        facts: dict = {"subject": subject.label, "stage": subject.stage, "target_hash": target.hash}
        facts |= {"stop": None, "round": None, "scoped": False, "lenses": [], "target": None, "diff": None}
        facts["note"] = target.note
        if isinstance(plan, review.Stop):
            facts["stop"] = plan.reason
        else:
            facts |= {"round": plan.n, "scoped": plan.scoped}
            names = self._lenses(subject, repo)
            facts["lenses"] = [{"name": n, "path": str(lenses.path_of(n, repo))} for n in names]
            if subject.stage != "branch" and target.text is not None:
                owner = subject.prefix.split(":")[0]
                facts["target"] = str(review.save_text(answers.state_dir(), owner, target.hash, target.text))
            if subject.stage == "branch" and not plan.scoped:
                facts["diff"] = target.text
            if plan.scoped and last is not None:
                facts["diff"], note = self._scoped_diff(subject, target, last, repo)
                facts["note"] = note or facts["note"]
        facts["last_round"] = None
        if last is not None:
            items = [item | {"status": _status(item, declined, accepted)} for item in last.data["findings"]]
            facts["last_round"] = {"n": int(last.subject.rsplit(":", 1)[1]), "findings": items}
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
        found = review.parse_findings(_read(parsed.get("findings", "")))
        if not parsed.get("target"):
            raise Refusal(f"--target is required; {USAGE}")
        subject = self._subject(parsed)
        store, repo = self._store_for_run(), self._repo or Path.cwd()
        rounds = review.rounds_of(subject.records, subject.prefix)
        target = self._target(store, subject, repo)
        if target.hash != parsed["target"]:
            raise Refusal(f"the target changed since context; run context again and review {target.hash}")
        plan = review.next_round(rounds, target.hash, review.declined(subject.records))
        if isinstance(plan, review.Stop):
            raise Refusal(f"no review round is due: {plan.reason}, {STOPS[plan.reason]}")
        owner = subject.prefix.split(":")[0]
        record = review.round_record(owner, subject.stage, plan, target.hash, review.ordered(found), target.head)
        for story in subject.stories:
            ops.ensure_record(store, story, record)
        p1 = sum(f.severity == "P1" for f in found)
        print(f"done: recorded round {plan.n} of {subject.label} {subject.stage}, {len(found)} findings, {p1} P1")

    def _resolve(self, parsed: dict) -> None:
        subject = self._subject(parsed)
        ref = subject.story["identifier"] if subject.story else subject.feature.live[0]["identifier"]
        if parsed["decline"]:
            asks = "; ".join(f"decide context finding {ref} --finding {id}" for id in parsed["decline"])
            raise Refusal(f"only the person declines a finding; ask with {asks}")
        if not parsed["accept"]:
            raise Refusal(f"resolve takes --accept ID; {USAGE}")
        store = self._store_for_run()
        last = review.last(review.rounds_of(subject.records, subject.prefix))
        held = {item["id"]: item for item in last.data["findings"]} if last else {}
        by_person = review.decided(subject.records)
        for id in parsed["accept"]:
            if id not in held:
                raise Refusal(f"{id} is not a finding of the last round of {subject.label} {subject.stage}")
            if id in by_person:
                raise Refusal(f"{id} is already decided: the person decided {by_person[id]}")
            if held[id].get("changes_delivery"):
                raise Refusal(
                    f"{id} changes what the story delivers; the person decides it: decide context finding"
                    f" {ref} --finding {id}"
                )
        for id in parsed["accept"]:
            for story in subject.stories:
                ops.ensure_record(store, story, review.accept_record(id, last.version))
        print(f"done: accepted {len(parsed['accept'])} findings on {subject.label} {subject.stage}")


@register("feature-review")
class FeatureReview(Review):
    stage_fixed = FEATURE


def _read(path: str) -> str:
    if not path:
        raise Refusal(f"--findings is required; {USAGE}")
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        raise Refusal(f"{path}: cannot be read") from None
    except UnicodeDecodeError:
        raise Refusal(f"{path}: not valid UTF-8") from None


def _line(item: dict) -> str:
    flag = ", changes delivery" if item.get("changes_delivery") else ""
    return f"- {item['severity']} {item['id']} {item['lens']} {item['path']}:{item['line']} {item['summary']}" + (
        f" ({item['status']}{flag})"
    )


def _print(facts: dict) -> None:
    head = f"review {facts['subject']} {facts['stage']}"
    if facts["stop"]:
        print(f"{head}: stop, {facts['stop']}: {STOPS[facts['stop']]}")
    else:
        scope = "scoped to the revision" if facts["scoped"] else "the whole target"
        print(f"{head}: round {facts['round']}, {scope}")
        print(f"target_hash: {facts['target_hash']}")
        print("lenses:")
        for lens in facts["lenses"]:
            print(f"- {lens['name']}: {lens['path']}")
        if facts["target"]:
            print(f"target: {facts['target']}")
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
