from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from deckhand import (
    answers,
    asks,
    checks,
    features,
    fleet,
    gaps,
    implplan,
    order,
    quiz,
    review,
    signoff,
    spikes,
    versions,
)
from deckhand.features import Feature, Phase
from deckhand.policy import Policy
from deckhand.records import Record
from deckhand.store import Store

DRAFT = "story-draft"
DRAFT_HAND, STORY_HAND = "draft", "story"
TAKEN = "draft-taken"
FEATURE_SKILL = "/deckhand:feature"
SIGNOFF_SKILL = "/deckhand:signoff"
STAGES = ("plan", "branch")
STALLED = "stalled"
RESTART, LEAVE = "Restart", "Leave it stopped"
POLICY = "policy"
POLICY_LINE = "its stories do not start until it is fixed"


@dataclass(frozen=True)
class Item:
    kind: str
    rank: int
    ref: str
    title: str
    at: str
    unblocks: int
    lines: list[str]
    next: list[str]
    head: str = ""


def policy_items(feature: Feature | None, clones: Mapping[str, Path], broken: Mapping[Path, str]) -> list[Item]:
    stories = feature.live if feature is not None else []
    held = {clones[label] for s in stories for label in s["labels"] if label in clones}
    named = sorted(held & set(broken), key=lambda clone: clone.name)
    return [Item(POLICY, 0, c.name, f"clone {c.name}", "", 0, [broken[c], POLICY_LINE], []) for c in named]


def _story_item(kind: str, rank: int, story: dict, at: str, lines: list[str], next: list[str], head: str = "") -> Item:
    first = f"{story['identifier']} {story['title']}"
    return Item(kind, rank, story["identifier"], story["title"], at, 0, [first, *lines], next, head)


def _signoff(story: dict, records: list[Record]) -> list[Item]:
    summary = versions.held(records, signoff.SUMMARY, story["id"])
    held = quiz.held_set(records, story["id"])
    head = summary.data.get("head") if summary else None
    if summary is None or held is None or not head or held.data.get("head") != head:
        return []
    version = signoff.version(records, story["id"], head)
    sign = versions.held(records, signoff.KIND, story["id"])
    if version is None or (sign is not None and sign.version == version):
        return []
    next = [f"{SIGNOFF_SKILL} {story['identifier']}"]
    return [_story_item(signoff.KIND, 0, story, summary.at, [f"ready to sign off at {head[:12]}"], next, head)]


def second_due(story: dict, records: list[Record], policy_for: Callable[[dict], Policy]) -> Record | None:
    sign = signoff.signed(records, story["id"])
    if sign is None:
        return None
    head = signoff.signed_head(sign)
    if not signoff.current(records, story["id"], head) or signoff.current(records, story["id"], head, second=True):
        return None
    return sign if policy_for(story).second_signoff else None


def _second(story: dict, records: list[Record], policy_for: Callable[[dict], Policy]) -> list[Item]:
    sign = second_due(story, records, policy_for)
    if sign is None:
        return []
    head = signoff.signed_head(sign)
    lines = [f"signed off at {head[:12]} by {story['assignee_name']}; a second sign-off is due"]
    next = [f"decide context second-sign-off {story['identifier']}"]
    return [_story_item(signoff.SECOND, 0, story, sign.at, lines, next, head)]


def finding_line(records: list[Record], finding: str) -> tuple[str, str]:
    found = review.locate(records, finding)
    if found is None:
        return "", f"finding {finding}"
    round, item = found
    return round.at, f"{item['severity']} {item['lens']} {item['path']}:{item['line']} {item['summary']}"


def _findings(story: dict, records: list[Record]) -> list[Item]:
    ref, found = story["identifier"], []
    proposed = set()
    for proposal in asks.open_proposals(records):
        proposed.add(proposal.subject)
        _, line = finding_line(records, proposal.subject)
        lines = [line, f"the hand proposes to decline it: {proposal.data.get('reason', '')}"]
        next = [f"decide context finding {ref} --finding {proposal.subject}"]
        found.append(_story_item(review.DECISION, 1, story, proposal.at, lines, next))
    for stage in STAGES:
        for finding in review.undecided_delivery(records, story["id"], stage):
            if finding["id"] in proposed:
                continue
            at, line = finding_line(records, finding["id"])
            lines = [line, "it changes what the story delivers"]
            next = [f"decide context finding {ref} --finding {finding['id']}"]
            found.append(_story_item(review.DECISION, 1, story, at, lines, next))
        found += _cap(story, records, stage)
    return found


def _cap(story: dict, records: list[Record], stage: str) -> list[Item]:
    held, _ = review.counted(records, f"{story['id']}:{stage}")
    latest = review.last(held)
    plan = versions.held(records, implplan.PURPOSE, story["id"])
    if latest is None or (stage == "plan" and plan is None):
        return []
    checked = versions.held(records, checks.KIND, story["id"])
    if stage == "plan" and plan:
        at = plan.version
    else:
        at = checked.version if checked is not None else review.reviewed(latest)
    if not review.at_cap(held, review.declined(records)) or review.cleared(records, story["id"], stage, at):
        return []
    lines = [f"the {stage} review reached its cap of {review.CAP} rounds"]
    next = [f"decide context review-cap {story['identifier']} --stage {stage}"]
    return [_story_item(review.DECISION, 1, story, latest.at, lines, next)]


def _subject_id(record: Record) -> str:
    return record.subject.rsplit(":", 1)[1]


def _questions(story: dict, records: list[Record], rank: int) -> list[Item]:
    found = []
    for held in records:
        qid = _subject_id(held) if held.kind == asks.QUESTION else ""
        if not qid or asks.answer_of(records, qid, asks.ANSWER) is not None:
            continue
        lines = [str(held.data.get("question", "")), "options: " + ", ".join(map(str, held.data.get("options", [])))]
        next = [f"decide context question {qid} --story {story['identifier']}"]
        found.append(_story_item(asks.ANSWER, rank, story, held.at, lines, next))
    return found


def _edits(story: dict, records: list[Record], rank: int) -> list[Item]:
    found = []
    for held in records:
        item = _subject_id(held) if held.kind == asks.EDIT_REQUEST else ""
        if not item or asks.answer_of(records, item, asks.EDIT) is not None:
            continue
        lines = [f"{held.data.get('path')}: {held.data.get('change')}", str(held.data.get("why", ""))]
        next = [f"decide context config-edit {story['identifier']} --item {item}"]
        found.append(_story_item(asks.EDIT, rank, story, held.at, lines, next))
    return found


def _prompt(story: dict, records: list[Record], session: fleet.Session | None, rank: int) -> list[Item]:
    if session is None or not fleet.alive(session) or not session.waiting_for:
        return []
    gap = gaps.latest(records) if session.waiting_for == fleet.PERMISSION else None
    lines = [f"{session.name} waits on {session.waiting_for}"]
    if gap is not None:
        lines.append(f"{gap.data.get('tool')}: {gap.data.get('command')}")
    at = gap.at if gap is not None else session.started_at
    return [_story_item("prompt", rank, story, at, lines, [f"claude attach {session.id}"])]


def _for_person(story: dict, records: list[Record], rank: int) -> list[Item]:
    found = _signoff(story, records) + _findings(story, records)
    return found + _questions(story, records, rank) + _edits(story, records, rank)


def last_start(records: list[Record], launch: str | None = None) -> Record | None:
    start = max((r for r in records if r.kind == asks.HAND_START), key=lambda r: r.at, default=None)
    return start if start is None or launch is None or start.data.get("kind") == launch else None


def finished(story: dict, records: list[Record], start: Record) -> bool:
    if start.data.get("kind") == DRAFT_HAND and any(r.kind == DRAFT and r.at >= start.at for r in records):
        return True
    if story["state"] != order.DONE:
        return False
    chores = signoff.after_items(records, story["id"])
    statuses = [signoff.after_status(records, story["id"], n, text)[0] for n, text in enumerate(chores, start=1)]
    return signoff.OPEN not in statuses


def merged_at(story: dict) -> str:
    return (story.get("completed_at") or "") if story["state"] == order.DONE else ""


def _waiting(story: dict, records: list[Record], second: bool) -> bool:
    if second or _for_person(story, records, 2) or signoff.after_merge_for_person(records, story["id"]):
        return True
    return asks.wake_due(records, story["id"], merged_at(story)) is not None


def stalled(
    story: dict, records: list[Record], session: fleet.Session | None, launch: str, second: bool = False
) -> bool:
    start = last_start(records, launch)
    if start is None or (session is not None and fleet.alive(session)):
        return False
    return not _waiting(story, records, second) and not finished(story, records, start)


def _new_since_start(records: list[Record], launch: str) -> bool:
    start = last_start(records, launch)
    quiet = (asks.HAND_START, STALLED)
    return start is not None and any(r.at > start.at and r.kind not in quiet for r in records)


def auto_restart(records: list[Record], launch: str, restarts: int | None) -> bool:
    return restarts == 0 or _new_since_start(records, launch)


def stall_answer(records: list[Record], launch: str) -> str | None:
    start = last_start(records, launch)
    if start is None:
        return None
    found = [r for r in records if r.kind == STALLED and r.version == start.at]
    return str(found[-1].data.get("answer")) if found else None


def _stalled(
    story: dict,
    records: list[Record],
    session: fleet.Session | None,
    rank: int,
    launch: str,
    second: bool,
    restarts: int | None,
) -> list[Item]:
    if not stalled(story, records, session, launch, second) or auto_restart(records, launch, restarts):
        return []
    start = last_start(records, launch)
    if start is None or stall_answer(records, launch) is not None:
        return []
    state = session.state if session is not None else "gone"
    lines = [f"its hand is {state}, and no record is new since it last started"]
    next = [f"decide context {STALLED} {story['identifier']}"]
    return [_story_item(STALLED, rank, story, start.at, lines, next)]


def _after_merge(store: Store, team: str, me: str) -> list[Item]:
    found = []
    for story, n, text, why, at in signoff.after_merge_queue(store, team, me):
        lines = [f"item {n}: {text}", f"left for the person: {why}"]
        next = [f"decide context after-merge {story['identifier']} --item {n}"]
        found.append(_story_item(signoff.AFTER, 2, story, at, lines, next))
    return found


def _day(date: str | None) -> str:
    return f"{date}T00:00:00.000Z" if date else ""


def _planning(store: Store, feature: Feature) -> list[Item]:
    found = features.approval_gaps(store, feature)
    if not found:
        return []
    name = feature.project["name"]
    return [Item("planning", 3, name, name, _day(feature.project["start_date"]), 0, [found[0]], [FEATURE_SKILL])]


def _spikes(store: Store, feature: Feature, read: fleet.Fleet) -> list[Item]:
    if feature.stories:
        return []
    project = feature.project["id"]
    found, answered = [], set()
    for document in spikes.answered(store, feature):
        key = spikes.key_of(project, document) or ""
        answered.add(key)
        lines = [f"spike answered, read it: {document['title']}"]
        found.append(Item("spike", 3, key, document["title"], document["at"], 0, lines, [document["url"]]))
    if read.note:
        return found
    asked = spikes.keys(answers.state_dir(), project)
    for key, session in sorted(fleet.spikes(read.sessions).items()):
        next = [f"claude attach {session.id}"]
        if key not in asked:
            continue
        if fleet.alive(session) and session.waiting_for:
            lines = [f"{session.name} waits on {session.waiting_for}"]
            found.append(Item("prompt", 4, key, session.name, session.started_at, 0, lines, next))
        elif fleet.failed(session) and key not in answered:
            lines = [f"{session.name} is {session.state} with no answer document"]
            found.append(Item(STALLED, 4, key, session.name, session.started_at, 0, lines, next))
    return found


def items(
    store: Store,
    team: str,
    feature: Feature | None,
    me: str,
    read: fleet.Fleet,
    policy_for: Callable[[dict], Policy],
    *,
    quiet: frozenset[str] = frozenset(),
    restarts: Mapping[str, int] | None = None,
) -> list[Item]:
    found = _after_merge(store, team, me)
    if feature is not None:
        found += _feature_items(store, feature, me, read, policy_for, quiet, restarts or {})
    return sorted(found, key=lambda i: (i.rank, -i.unblocks, i.at, i.ref))


def _feature_items(
    store: Store,
    feature: Feature,
    me: str,
    read: fleet.Fleet,
    policy_for: Callable[[dict], Policy],
    quiet: frozenset[str],
    restarts: Mapping[str, int],
) -> list[Item]:
    building = features.phase(feature) is Phase.BUILDING
    rank = 2 if building else 4
    edges = order.edges(store, feature)
    open_ids = {s["id"] for s in feature.live if s["state"] not in features.FINISHED}
    hands = fleet.hands(read.sessions)
    weights = {s["identifier"]: order.unblocks(s["id"], edges, open_ids) for s in feature.live}
    found: list[Item] = []
    for story in feature.live:
        records = feature.records.get(story["id"], [])
        if story["assignee"] == me:
            found += _for_person(story, records, rank)
            if not read.note:
                session = hands.get(story["identifier"])
                second = second_due(story, records, policy_for) is not None
                launch = STORY_HAND if building or story["state"] == order.DONE else DRAFT_HAND
                found += _prompt(story, records, session, rank)
                if story["identifier"] not in quiet:
                    found += _stalled(story, records, session, rank, launch, second, restarts.get(story["identifier"]))
        elif story["assignee"] is not None:
            found += _second(story, records, policy_for)
    found = [replace(i, unblocks=weights.get(i.ref, 0)) for i in found]
    if not building:
        found += _planning(store, feature) + _spikes(store, feature, read)
    return found
