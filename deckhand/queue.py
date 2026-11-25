from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from deckhand import (
    answers,
    asks,
    chores,
    features,
    fleet,
    gaps,
    order,
    planning,
    review,
    review_gaps,
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
DROPPED_DRAFT = "draft-dropped"
TAKEN_KINDS = (TAKEN, DROPPED_DRAFT)
FEATURE_SKILL = "/deckhand:feature"
READY = "the feature is ready for approval"
REAPPROVE = "the plan changed since approval; approve it again"
SIGNOFF_SKILL = "/deckhand:signoff"
STAGES = ("plan", "branch")
SPIKE = "spike"
POLICY = "policy"
# Below every story's item, so a policy the person puts off never holds back work already in flight.
POLICY_RANK = 5
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
    named = sorted({label for s in stories for label in s["labels"] if label in clones and clones[label] in broken})
    return [
        Item(POLICY, POLICY_RANK, label, f"clone {label}", "", 0, [broken[clones[label]], POLICY_LINE], [])
        for label in named
    ]


def _story_item(kind: str, rank: int, story: dict, at: str, lines: list[str], next: list[str], head: str = "") -> Item:
    first = f"{story['identifier']} {story['title']}"
    return Item(kind, rank, story["identifier"], story["title"], at, 0, [first, *lines], next, head)


def _signoff(story: dict, records: list[Record]) -> list[Item]:
    summary = versions.held(records, signoff.SUMMARY, story["id"])
    head = summary.data.get("head") if summary else None
    version = signoff.version_at(records, story["id"], head) if isinstance(head, str) else None
    sign = versions.held(records, signoff.KIND, story["id"])
    if summary is None or version is None or (sign is not None and sign.version == version):
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


def finding_lines(records: list[Record], finding: str) -> list[str]:
    _, line = finding_line(records, finding)
    found = review.locate(records, finding)
    lines = [line] + ([review_gaps.delivery_line(found[1])] if found and found[1].get("changes_delivery") else [])
    proposal = asks.proposed(records, finding)
    return lines + ([asks.proposal_line(proposal, _of_feature(records, finding))] if proposal else [])


def _of_feature(records: list[Record], finding: str) -> bool:
    found = review.locate(records, finding)
    return found is not None and review.cap_prefix(found[0].subject).endswith(":feature")


def _findings(story: dict, records: list[Record]) -> list[Item]:
    ref, found = story["identifier"], []
    for proposal in asks.open_proposals(records):
        if _of_feature(records, proposal.subject):
            continue
        lines = finding_lines(records, proposal.subject)
        next = [f"decide context finding {ref} --finding {proposal.subject}"]
        found.append(_story_item(review.DECISION, 1, story, proposal.at, lines, next))
    for stage in STAGES:
        found += _cap(story, records, stage)
    return found


def _feature_records(feature: Feature) -> list[Record]:
    return [r for s in feature.live for r in feature.records.get(s["id"], [])]


def _feature_proposals(feature: Feature) -> list[Record]:
    records = _feature_records(feature)
    return [p for p in asks.open_proposals(records) if _of_feature(records, p.subject)]


def _feature_findings(feature: Feature) -> list[Item]:
    records = _feature_records(feature)
    project, name = feature.project["id"], feature.project["name"]
    found = []
    for proposal in _feature_proposals(feature):
        lines = [name, *finding_lines(records, proposal.subject)]
        next = [f"decide context finding {project} --finding {proposal.subject}"]
        found.append(Item(review.DECISION, 1, name, name, proposal.at, 0, lines, next))
    return found


def _capped(records: list[Record], prefix: str) -> Record | None:
    held, _ = review.counted(records, prefix)
    latest = review.last(held)
    capped = review.at_cap(held, review.excused(records, prefix), review.proceeded(records, prefix))
    return latest if capped else None


def _feature_cap(feature: Feature) -> list[Item]:
    project, name = feature.project["id"], feature.project["name"]
    latest = _capped(_feature_records(feature), f"{project}:feature")
    if latest is None:
        return []
    lines = ["three feature review rounds left a P1 open"]
    next = [f"decide context review-cap {project} --stage feature"]
    return [Item(review.DECISION, 1, name, name, latest.at, 0, lines, next)]


def _cap(story: dict, records: list[Record], stage: str) -> list[Item]:
    latest = _capped(records, f"{story['id']}:{stage}")
    if latest is None:
        return []
    lines = [f"three {stage} review rounds left a P1 open"]
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
    findings = _findings(story, records)
    found = ([] if findings else _signoff(story, records)) + findings
    return found + _questions(story, records, rank) + _edits(story, records, rank)


def last_start(records: list[Record], launch: str | None = None) -> Record | None:
    start = max((r for r in records if r.kind == asks.HAND_START), key=lambda r: r.at, default=None)
    return start if start is None or launch is None or start.data.get("kind") == launch else None


def merged_at(story: dict) -> str:
    return (story.get("completed_at") or "") if story["state"] == order.DONE else ""


def _after_merge(held: list[Feature], me: str) -> list[Item]:
    found = []
    for story, n, text, why, at in chores.after_merge_queue(held, me):
        lines = [f"item {n}: {text}", f"left for the person: {why}"]
        next = [f"decide context after-merge {story['identifier']} --item {n}"]
        found.append(_story_item(chores.AFTER, 2, story, at, lines, next))
    return found


def _day(date: str | None) -> str:
    return f"{date}T00:00:00.000Z" if date else ""


def _planning(store: Store, feature: Feature, cap: list[Item]) -> list[Item]:
    if not features.replanning(feature):
        return []
    own = {p.subject for p in _feature_proposals(feature)}
    typed = features.review_gap_list(store, feature)
    shown = [g for g in typed if not (g.kind == review_gaps.P1_KIND and (cap or g.id in own))]
    plain = features.plain_gaps(store, feature)
    covered = len(shown) < len(typed)
    found = [*plain, *(g.person for g in shown)] or (
        [] if covered else [REAPPROVE if feature.reapproval_due else READY]
    )
    if not found:
        return []
    name = feature.project["name"]
    return [Item("planning", 3, name, name, _day(feature.project["start_date"]), 0, [found[0]], [FEATURE_SKILL])]


def _spikes(
    store: Store,
    feature: Feature,
    read: fleet.Fleet,
    restarts: Mapping[str, int],
    idle: frozenset[str],
    quiet: frozenset[str],
) -> list[Item]:
    from deckhand import stalls

    if feature.stories:
        return []
    project = feature.project["id"]
    found, answered = [], set()
    facts = planning.saved_at(store, feature, "Facts")
    for document in spikes.answered(store, feature):
        key = spikes.key_of(project, document) or ""
        answered.add(key)
        if facts is not None and facts > document["at"]:
            continue
        lines = [f"spike answered, read it: {document['title']}"]
        found.append(Item(SPIKE, 3, key, document["title"], document["at"], 0, lines, [document["url"]]))
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
        elif key in answered or key in quiet or restarts.get(key) == 0:
            continue
        elif fleet.failed(session) or key in idle:
            lines = [f"{session.name} is {'idle' if key in idle else session.state} with no answer document"]
            found.append(Item(stalls.STALLED, 4, key, session.name, session.started_at, 0, lines, next))
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
    idle: frozenset[str] = frozenset(),
    elsewhere: Sequence[Feature] = (),
) -> list[Item]:
    found = _after_merge([*([feature] if feature is not None else []), *elsewhere], me)
    if feature is not None:
        found += _feature_items(store, feature, me, read, policy_for, quiet, restarts or {}, idle)
    for chore in elsewhere:
        found += _chore_items(chore, me, read)
    return ranked(found)


# A chore hand in an older feature still asks the person, so its asks and prompts reach the queue.
def _chore_items(feature: Feature, me: str, read: fleet.Fleet) -> list[Item]:
    hands, found = fleet.hands(read.sessions), []
    for story in (s for s in feature.live if s["state"] == order.DONE and s["assignee"] == me):
        records = feature.records.get(story["id"], [])
        found += _for_person(story, records, 2)
        found += [] if read.note else _prompt(story, records, hands.get(story["identifier"]), 2)
    return found


def ranked(found: list[Item]) -> list[Item]:
    return sorted(found, key=lambda i: (i.rank, -i.unblocks, i.at, i.ref))


def _feature_items(
    store: Store,
    feature: Feature,
    me: str,
    read: fleet.Fleet,
    policy_for: Callable[[dict], Policy],
    quiet: frozenset[str],
    restarts: Mapping[str, int],
    idle: frozenset[str],
) -> list[Item]:
    from deckhand import stalls

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
                drafting = not building and story["state"] != order.DONE or features.needs_draft(feature, story)
                launch = DRAFT_HAND if drafting else STORY_HAND
                found += _prompt(story, records, session, rank)
                ref = story["identifier"]
                if ref not in quiet:
                    found += stalls.items(story, records, session, rank, launch, second, restarts.get(ref), ref in idle)
        elif story["assignee"] is not None:
            found += _second(story, records, policy_for)
    found = [replace(i, unblocks=weights.get(i.ref, 0)) for i in found] + _feature_findings(feature)
    cap = _feature_cap(feature) if features.replanning(feature) else []
    found += cap + _planning(store, feature, cap)
    if not building:
        found += _spikes(store, feature, read, restarts, idle, quiet)
    return found
