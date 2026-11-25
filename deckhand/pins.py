from __future__ import annotations

from deckhand import asks, features, planning, queue, quiz, review, signoff, startable, versions
from deckhand.decisions import KINDS, Pin
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.store import Store

ITEM_KINDS = {quiz.KIND: "N", signoff.AFTER: "N", asks.EDIT: "ID"}
STORY_ARGUMENT = ("decision-item", asks.ANSWER)


def _number(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise Refusal("--item takes an item number from 1")
    return int(text)


def _label(story: dict) -> str:
    return f"{story['identifier']} {story['title']}"


def _finding_pin(store: Store, feature: features.Feature, ref: str, finding: str) -> Pin:
    story = features.story(store, feature, ref)
    found = review.locate(feature.records.get(story["id"], []), finding)
    if found is None:
        raise Refusal(f"{finding} is not a finding of any review round on {story['identifier']}")
    round, _ = found
    detail = "; ".join(queue.finding_lines(feature.records.get(story["id"], []), finding))
    if round.subject.startswith(f"{feature.project['id']}:feature:"):
        live = feature.live
        labels = [_label(s) for s in live]
        return Pin(finding, round.version, [s["id"] for s in live], detail, feature.project["name"], labels)
    return Pin(finding, round.version, [story["id"]], detail, _label(story), [_label(story)])


def _cap_pin(store: Store, feature: features.Feature, ref: str, stage: str) -> Pin:
    story = features.story(store, feature, ref)
    records = feature.records.get(story["id"], [])
    prefix = f"{story['id']}:{stage}"
    held, _ = review.counted(records, prefix)
    excused = review.excused(records, prefix)
    latest = review.last(held)
    since = review.proceeded(records, prefix)
    if latest is None or not review.at_cap(held, excused, since):
        raise Refusal(f"the {stage} review of {story['identifier']} has not reached its cap with a P1 open")
    open_count = len(review.open_p1(latest, excused))
    at = review.reviewed(latest)[:12]
    detail = f"{review.p1_rounds(held, excused, since)} rounds left a P1 open, {open_count} P1 open at {at}"
    subject, version = review.cap_subject(latest), review.cap_version(latest)
    return Pin(subject, version, [story["id"]], detail, _label(story), [_label(story)])


def _signoff_pin(store: Store, feature: features.Feature, ref: str, head: str) -> Pin:
    story = features.story(store, feature, ref)
    records = feature.records.get(story["id"], [])
    missing = signoff.gaps(records, story["id"], head)
    version = signoff.version(records, story["id"], head)
    label = _label(story)
    if missing and version is None:
        raise Refusal(f"{story['identifier']} is not ready for sign-off: " + "; ".join(missing))
    if missing:
        detail = f"head {head}, not ready for sign-off"
        return Pin(story["id"], version, [story["id"]], detail, label, [label], [signoff.NOT_YET, signoff.BACK])
    return Pin(story["id"], version, [story["id"]], f"head {head}", label, [label])


def _second_pin(store: Store, feature: features.Feature, ref: str) -> Pin:
    story = features.story(store, feature, ref)
    records = feature.records.get(story["id"], [])
    held = signoff.signed(records, story["id"])
    head = signoff.signed_head(held) if held else ""
    if held is None or not signoff.current(records, story["id"], head):
        reason = signoff.standing(records, story["id"], head) if held else "the person has not signed off"
        raise Refusal(f"{story['identifier']} has no current sign-off to second: {reason}")
    label = _label(story)
    return Pin(story["id"], signoff.second_version(held), [story["id"]], f"head {head}", label, [label])


def _after_pin(store: Store, feature: features.Feature, ref: str, n: int) -> Pin:
    story = features.story(store, feature, ref)
    records = feature.records.get(story["id"], [])
    items = signoff.after_items(records, story["id"])
    if n > len(items):
        raise Refusal(f"{story['identifier']} has no after-merge item {n}; it has {len(items)}")
    status, held = signoff.after_status(records, story["id"], n, items[n - 1])
    if status != signoff.FOR_PERSON or held is None:
        raise Refusal(f"item {n} of {story['identifier']} is not left for the person; it is {status}")
    label = _label(story)
    detail = f"item {n}, {items[n - 1]} ({held.data.get('why')})"
    return Pin(held.subject, held.version, [story["id"]], detail, label, [label])


def _quiz_pin(store: Store, feature: features.Feature, ref: str, n: int) -> Pin:
    story = features.story(store, feature, ref)
    records = feature.records.get(story["id"], [])
    held = quiz.held_set(records, story["id"])
    if held is None:
        raise Refusal(f"no quiz is saved for {story['identifier']}")
    items = quiz.items_of(held)
    if not 1 <= n <= len(items):
        raise Refusal(f"the quiz on {story['identifier']} has no item {n}; it has {len(items)}")
    if quiz.right(records, story["id"], n):
        raise Refusal(f"item {n} of the quiz on {story['identifier']} is already answered right")
    item = items[n - 1]
    detail = f"item {n} of {len(items)}, {item.question.rstrip('?')}"
    if quiz.missed(records, story["id"], n):
        detail += f" (the code is at {item.where})"
    label = _label(story)
    options = quiz.shown_options(held.version, n, item.options)
    return Pin(quiz.subject(story["id"], n), held.version, [story["id"]], detail, label, [label], options)


def _find(records: list[Record], kind: str, subject: str, what: str, ref: str) -> Record:
    found = next((r for r in reversed(records) if r.kind == kind and r.subject == subject), None)
    if found is None:
        raise Refusal(f"no {what} on {ref}")
    return found


def _question_pin(store: Store, feature: features.Feature, ref: str, qid: str) -> Pin:
    story = features.story(store, feature, ref)
    records = feature.records.get(story["id"], [])
    held = _find(records, asks.QUESTION, f"{story['id']}:{qid}", f"question {qid}", story["identifier"])
    if asks.answer_of(records, qid, asks.ANSWER):
        raise Refusal(f"question {qid} on {story['identifier']} is already answered")
    detail = f"{held.data['question']} {held.data['why']}"
    label = _label(story)
    return Pin(qid, held.version, [story["id"]], detail, label, [label], held.data["options"])


def _edit_pin(store: Store, feature: features.Feature, ref: str, item: str) -> Pin:
    story = features.story(store, feature, ref)
    records = feature.records.get(story["id"], [])
    held = _find(records, asks.EDIT_REQUEST, f"{story['id']}:{item}", f"config edit {item}", story["identifier"])
    if asks.answer_of(records, item, asks.EDIT):
        raise Refusal(f"config edit {item} on {story['identifier']} is already settled")
    data = held.data
    detail = f"{data['path']}: {data['change']} ({data['why']})"
    label = _label(story)
    return Pin(item, held.version, [story["id"]], detail, label, [label])


def _stalled_pin(store: Store, feature: features.Feature, ref: str) -> Pin:
    story = features.story(store, feature, ref)
    records = feature.records.get(story["id"], [])
    launch = startable.launch_of(startable.kind(feature, story))
    start = queue.last_start(records, launch)
    if start is None:
        raise Refusal(f"{story['identifier']} has no hand started")
    if queue.stall_answer(records, launch) is not None:
        raise Refusal(f"the stop of {story['identifier']}'s hand since it last started is already settled")
    detail = "its hand stopped and has written nothing since it last started. Restart it"
    return Pin(story["id"], start.at, [story["id"]], detail, story["identifier"], [story["identifier"]])


def pin(
    store: Store,
    feature: features.Feature,
    kind: str,
    subject: str,
    stories: list[str],
    finding: str = "",
    item: str | None = None,
    stage: str = "",
    head: str = "",
) -> Pin:
    if kind not in KINDS:
        raise Refusal(f"unknown decision kind {kind!r}, one of: {', '.join(KINDS)}")
    if (kind == "finding") != bool(finding):
        raise Refusal("finding needs --finding ID, and --finding applies only to finding")
    if kind in ITEM_KINDS and item is None:
        raise Refusal(f"{kind} needs --item {ITEM_KINDS[kind]}")
    if kind not in ITEM_KINDS and item is not None:
        raise Refusal(f"--item applies only to {', '.join(ITEM_KINDS)}")
    if kind not in STORY_ARGUMENT and stories:
        raise Refusal(f"--story applies only to {' and '.join(STORY_ARGUMENT)}; every other kind finds its own stories")
    if stage and kind != review.CAP_DECISION:
        raise Refusal("--stage applies only to review-cap")
    if kind == review.CAP_DECISION:
        if stage not in ("", "plan", "branch"):
            raise Refusal("--stage takes plan or branch")
        return _cap_pin(store, feature, subject, stage or "plan")
    if kind == signoff.KIND:
        if not head:
            raise Refusal("sign-off needs the head of the story's worktree")
        return _signoff_pin(store, feature, subject, head)
    if kind == signoff.SECOND:
        return _second_pin(store, feature, subject)
    if kind == signoff.AFTER:
        return _after_pin(store, feature, subject, _number(item or ""))
    if kind == "finding":
        return _finding_pin(store, feature, subject, finding)
    if kind == quiz.KIND:
        return _quiz_pin(store, feature, subject, _number(item or ""))
    if kind == asks.EDIT:
        return _edit_pin(store, feature, subject, item or "")
    if kind == queue.STALLED:
        return _stalled_pin(store, feature, subject)
    if kind == asks.ANSWER:
        if len(stories) != 1:
            raise Refusal("question needs exactly one --story")
        return _question_pin(store, feature, stories[0], subject)
    if kind == "decision-item":
        if not stories:
            raise Refusal("decision-item needs at least one --story")
        held = [features.story(store, feature, ref) for ref in stories]
        version = versions.version_for(store, feature, kind, subject)
        ids, labels = [s["id"] for s in held], [_label(s) for s in held]
        return Pin(versions.item_subject(subject), version, ids, subject, "", labels)
    if kind in versions.STORY_KINDS:
        story = features.story(store, feature, subject)
        version = versions.version_for(store, feature, kind, story["id"])
        return Pin(story["id"], version, [story["id"]], "", _label(story), [_label(story)])
    project = feature.project["id"]
    if kind in versions.SECTION_OF and planning.section(store, feature, versions.SECTION_OF[kind]) is None:
        raise Refusal(f"the {versions.SECTION_OF[kind]} section is not saved; save it before approving it")
    if subject != project:
        raise Refusal(f"{subject} is not the open feature; the open feature is {features.describe(feature.project)}")
    if kind == features.APPROVAL:
        gaps = features.approval_gaps(store, feature)
        if gaps:
            raise Refusal("the feature is not ready for approval: " + "; ".join(gaps[:3]))
    live = feature.live
    if not live:
        raise Refusal(f"{features.describe(feature.project)} has no stories to hold the decision")
    version = versions.version_for(store, feature, kind, project)
    return Pin(project, version, [s["id"] for s in live], "", feature.project["name"], [_label(s) for s in live])
