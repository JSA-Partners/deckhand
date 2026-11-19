from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import asks, features, fleet, gaps, implplan, queue, quiz, review, signoff, spikes
from deckhand.features import FEATURE_LABEL
from deckhand.fleet import Fleet, Session
from deckhand.ops import create_id
from deckhand.policy import Policy
from deckhand.records import HAND, Record, iso
from deckhand.store.fake import FakeStore
from deckhand.summary import ChangeSummary, digest
from tests.test_feature_approval import approve_everything, setup
from tests.test_implplan import manifest
from tests.test_step_build import repo, state
from tests.test_step_review import raw
from tests.test_step_signoff import ready, sign

__all__ = ["repo", "state"]

ME = "viewer-1"
OTHER = "someone-else"
EMPTY = ChangeSummary([], [], [], None)


class Clock:
    def __init__(self) -> None:
        self.ms = 1795000000000

    def __call__(self) -> str:
        self.ms += 1000
        return iso(self.ms)


def alpha(planning: bool = False) -> FakeStore:
    store = setup(FakeStore(now=Clock()))
    if not planning:
        approve_everything(store)
    store.assign("s1", ME)
    store.assign("s2", ME)
    return store


def waiting(store: FakeStore, me: str = ME, sessions: tuple = (), note: str = "", second: bool = False):
    feature = features.open_feature(store, "ACME")
    policy = Policy("widgets", second_signoff=second)
    return queue.items(store, "ACME", feature, me, Fleet(list(sessions), note), lambda story: policy)


def kinds(items: list[queue.Item]) -> list[tuple[str, int, str]]:
    return [(i.kind, i.rank, i.ref) for i in items]


def hand(ref: str, state: str, status: str | None, waiting_for: str | None = None, at: str = "") -> Session:
    return Session(f"id-{ref}", fleet.hand_name(ref), f"sid-{ref}", "/w", at, state, status, waiting_for)


def summarized(store: FakeStore, story: str, head: str) -> None:
    store.put_record(story, signoff.summary_record(story, head, EMPTY))
    store.put_record(story, quiz.set_record(story, head, [], digest(EMPTY), 0))


def asked(store: FakeStore, story: str, text: str = "Which queue?") -> None:
    found = asks.parse_question(json.dumps({"question": text, "options": ["Redis", "SQS"], "why": "Both fit."}))
    store.put_record(story, asks.question_record(story, found))


def started(store: FakeStore, story: str) -> None:
    store.put_record(story, Record(asks.HAND_START, story, "v1", HAND, {"kind": "story", "session": "hand"}))


def chore(store: FakeStore, story: str) -> None:
    store.put_record(story, Record(implplan.PURPOSE, story, "p1", "agent:implplan", manifest(after_merge=["rotate"])))
    store.put_record(story, signoff.after_record(signoff.AFTER_PERSON, story, 1, "rotate", {"why": "the vault"}))
    store.set_issue_state(story, "Done")


def every_kind(store: FakeStore) -> tuple:
    summarized(store, "s1", "c2")
    store.put_record("s1", asks.proposal_record("f1", "v1", "out of scope"))
    edit = asks.parse_edit(json.dumps({"path": ".claude/settings.json", "change": "allow npm", "why": "tests"}))
    store.put_record("s1", asks.edit_record("s1", edit))
    asked(store, "s1")
    chore(store, "s2")
    started(store, "s1")
    return (hand("ACME-1", "failed", None),)


def test_rank_order_across_all_kinds():
    store = alpha(planning=True)
    sessions = every_kind(store)
    assert kinds(waiting(store, sessions=sessions)) == [
        ("sign-off", 0, "ACME-1"),
        ("finding", 1, "ACME-1"),
        ("config-edit", 2, "ACME-1"),
        ("after-merge", 2, "ACME-2"),
        ("planning", 3, "Alpha"),
        ("question", 4, "ACME-1"),
    ]


def test_question_and_stalled_rank_with_blocked_hands_in_build():
    store = alpha()
    started(store, "s2")
    asked(store, "s1")
    blocked = hand("ACME-1", "blocked", "waiting", fleet.PERMISSION, iso(1795000999000))
    found = waiting(store, sessions=(blocked, hand("ACME-2", "failed", None)))
    assert kinds(found) == [("stalled", 2, "ACME-2"), ("question", 2, "ACME-1"), ("prompt", 2, "ACME-1")]
    assert found[2].next == ["claude attach id-ACME-1"]
    assert found[1].next[0].startswith("decide context question ")


def test_ties_unblocks_then_oldest():
    store = alpha()
    asked(store, "s2")
    early = hand("ACME-1", "blocked", "waiting", fleet.PERMISSION, iso(1795000000500))
    assert kinds(waiting(store, sessions=(early,))) == [("prompt", 2, "ACME-1"), ("question", 2, "ACME-2")]
    late = hand("ACME-1", "blocked", "waiting", fleet.PERMISSION, iso(1795009999000))
    assert kinds(waiting(store, sessions=(late,))) == [("question", 2, "ACME-2"), ("prompt", 2, "ACME-1")]
    store.relate("s1", "s2")
    found = waiting(store, sessions=(late,))
    assert kinds(found) == [("prompt", 2, "ACME-1"), ("question", 2, "ACME-2")]
    assert [i.unblocks for i in found] == [1, 0]


def test_other_owners_story_items_hidden():
    store = alpha()
    every_kind(store)
    started(store, "s1")
    sessions = (hand("ACME-1", "failed", None),)
    assert kinds(waiting(store, me=OTHER, sessions=sessions)) == []
    store.assign("s1", None)
    store.assign("s2", None)
    assert kinds(waiting(store, sessions=sessions)) == []


def test_planning_items_shown_to_everyone():
    store = alpha(planning=True)
    assert kinds(waiting(store, me=OTHER)) == [("planning", 3, "Alpha")]
    [item] = waiting(store, me=OTHER)
    assert item.next == ["/deckhand:feature"]
    assert item.lines == ["the Contract is not approved"]
    assert item.at == "2026-11-16T00:00:00.000Z"


def test_second_signoff_shown_only_to_others(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    assert kinds(waiting(store, second=True)) == []
    assert kinds(waiting(store, me=OTHER, second=True)) == [("second-sign-off", 0, "ACME-1")]
    assert waiting(store, me=OTHER, second=True)[0].next == ["decide context second-sign-off ACME-1"]
    assert kinds(waiting(store, me=OTHER)) == []


def test_void_is_not_a_signoff_item_until_new_summary():
    store = alpha()
    summarized(store, "s1", "c2")
    [item] = waiting(store)
    assert (item.kind, item.next) == ("sign-off", ["/deckhand:signoff ACME-1"])
    version = signoff.version(store.records("s1"), "s1", "c2")
    held = Record(signoff.KIND, "s1", version, review.PERSON + "s", {"answer": "Sign off", "nonce": "n1", "by": ME})
    store.put_record("s1", held)
    assert waiting(store) == []
    store.put_record("s1", signoff.void_record("s1", held, "the head moved"))
    assert waiting(store) == []
    summarized(store, "s1", "c3")
    assert [(i.kind, i.lines[1]) for i in waiting(store)] == [("sign-off", "ready to sign off at c3")]


def test_failed_hand_is_stalled_item():
    store = alpha()
    started(store, "s1")
    [start] = [r for r in store.records("s1") if r.kind == asks.HAND_START]
    for session in (hand("ACME-1", "failed", None), hand("ACME-1", "stopped", None)):
        [item] = waiting(store, sessions=(session,))
        assert (item.kind, item.rank, item.at, item.next) == ("stalled", 2, start.at, ["hand apply start ACME-1"])
    assert waiting(store, sessions=(hand("ACME-1", "working", "busy"),)) == []
    assert waiting(store, sessions=(hand("ACME-1", "failed", None),), note="the fleet read failed") == []
    store.put_record("s1", Record("checks", "s1", "c2", HAND, {"passed": False}))
    assert waiting(store, sessions=(hand("ACME-1", "failed", None),)) == []
    assert queue.restart_due(store.issue("s1") | {"state": "In Progress"}, store.records("s1"), None)
    started(store, "s1")
    assert not queue.restart_due(store.issue("s1") | {"state": "In Progress"}, store.records("s1"), None)


def test_finished_hand_is_not_stalled():
    store = alpha(planning=True)
    started(store, "s1")
    records = store.records("s1")
    assert queue.stalled(store.issue("s1") | {"state": "Backlog"}, records, None)
    store.put_record("s1", Record(queue.DRAFT, "s1", "d1", HAND, {}))
    assert not queue.stalled(store.issue("s1") | {"state": "Backlog"}, store.records("s1"), None)
    done = alpha()
    started(done, "s2")
    assert not queue.stalled(done.issue("s2") | {"state": "Done"}, done.records("s2"), None)
    asked(done, "s2")
    assert not queue.stalled(done.issue("s2") | {"state": "Backlog"}, done.records("s2"), None)


QUESTION = "Does the queue keep order?"


def asked_spike(state: Path, key: str, question: str = QUESTION) -> None:
    (state / "spikes").mkdir(parents=True, exist_ok=True)
    (state / "spikes" / f"{key}.md").write_text(question)


def spiked(state: Path) -> tuple[FakeStore, str, str]:
    store = FakeStore(now=Clock())
    project = create_id("ACME:Beta", "feature")
    store.create_project(project, "ACME", "Beta", "", [FEATURE_LABEL])
    store.set_project_dates(project, "2026-11-16", "2026-11-23")
    key = spikes.key(project, QUESTION)
    asked_spike(state, key)
    return store, project, key


def answer(store: FakeStore, project: str, key: str, title: str = "Spike: Does the queue keep order?") -> None:
    store.put_document(spikes.answer_id(project, key), project, title, "Yes.")


def test_spike_answer_is_planning_item_until_story_map(state: Path):
    store, project, key = spiked(state)
    answer(store, project, key)
    found = [i for i in waiting(store, me=OTHER) if i.kind == "spike"]
    [doc] = store.project_documents(project)
    assert [(i.rank, i.ref, i.at, i.next) for i in found] == [(3, key, doc["at"], [doc["url"]])]
    store.create_issue("s9", "ACME", "Orders", "Outcome.", project, [])
    assert [i for i in waiting(store, me=OTHER) if i.kind == "spike"] == []


def test_renamed_spike_document_not_counted(state: Path):
    store, project, key = spiked(state)
    answer(store, project, key, "Spike: Does the queue keep order? Renamed")
    store.put_document("loose", project, "Spike: Does the queue keep order?", "Yes.")
    assert [i for i in waiting(store) if i.kind == "spike"] == []


def test_failed_spike_hand_is_stalled(state: Path):
    store, project, key = spiked(state)
    gone = Session("id-s", fleet.spike_name(key), "sid", "/w", iso(1795000000000), "failed", None, None)
    found = [i for i in waiting(store, me=OTHER, sessions=(gone,)) if i.kind == "stalled"]
    assert [(i.rank, i.ref, i.next) for i in found] == [(4, key, ["claude attach id-s"])]
    working = Session("id-s", fleet.spike_name(key), "sid", "/w", iso(1795000000000), "working", "busy", None)
    assert [i for i in waiting(store, sessions=(working,)) if i.kind == "stalled"] == []
    assert [i for i in waiting(store, sessions=(gone,), note="the fleet read failed") if i.kind == "stalled"] == []
    answer(store, project, key)
    assert [i.kind for i in waiting(store, sessions=(gone,))] == ["planning", "spike"]


def test_spike_of_another_feature_never_shows(state: Path):
    store, project, key = spiked(state)
    earlier = spikes.key(create_id("ACME:Alpha", "feature"), QUESTION)
    asked_spike(state, earlier)
    stray = spikes.key(project, "Was this asked here?")
    rows = [
        Session("id-a", fleet.spike_name(earlier), "sid", "/w", iso(1795000000000), "failed", None, None),
        Session(
            "id-b",
            fleet.spike_name(earlier + "x"),
            "sid",
            "/w",
            iso(1795000000000),
            "blocked",
            "waiting",
            fleet.PERMISSION,
        ),
        Session("id-c", fleet.spike_name(stray), "sid", "/w", iso(1795000000000), "failed", None, None),
        Session("id-d", fleet.spike_name(key), "sid", "/w", iso(1795000000000), "failed", None, None),
    ]
    found = [(i.kind, i.ref) for i in waiting(store, me=OTHER, sessions=tuple(rows)) if i.kind != "planning"]
    assert found == [("stalled", key)]


def test_prompt_ranks_with_blocked_hands_in_planning():
    store = alpha(planning=True)
    blocked = hand("ACME-1", "blocked", "waiting", fleet.PERMISSION, iso(1795000000500))
    assert ("prompt", 4, "ACME-1") in kinds(waiting(store, sessions=(blocked,)))
    approve_everything(store)
    assert kinds(waiting(store, sessions=(blocked,))) == [("prompt", 2, "ACME-1")]


def test_second_signoff_due_is_waiting_not_stalled(
    tmp_path: Path, repo: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = ready(tmp_path, repo, state, capsys)
    sign(store, repo, state, capsys)
    store.put_record("s1", Record(asks.WAKE, "s1", "w1", HAND, {}))
    started(store, "s1")
    failed = (hand("ACME-1", "failed", None),)
    assert [i.kind for i in waiting(store, sessions=failed, second=True)] == []
    assert [i.kind for i in waiting(store, sessions=failed)] == ["stalled"]
    story, records = store.issue("s1") | {"state": "In Review"}, store.records("s1")
    store.put_record("s1", Record("checks", "s1", "x", "agent:checks", {"head": "x", "passed": False}))
    later = store.records("s1")
    assert not queue.stalled(story, records, None, second=True)
    assert not queue.restart_due(story, later, None, second=True)
    assert queue.restart_due(story, later, None)


def test_released_feature_chore_stays_in_the_queue():
    store = alpha()
    chore(store, "s1")
    store.set_issue_state("s2", "Done")
    assert features.open_feature(store, "ACME") is None
    found = queue.items(store, "ACME", None, ME, Fleet(), lambda story: Policy("widgets"))
    assert [(i.kind, i.ref, i.next) for i in found] == [
        ("after-merge", "ACME-1", ["decide context after-merge ACME-1 --item 1"])
    ]


def rounds(store: FakeStore, stage: str, *items: dict, count: int = 3) -> Record:
    found = review.parse_findings(json.dumps(list(items)))
    for n in range(1, count + 1):
        plan = review.RoundPlan(n, scoped=n > 1, base_hash="")
        store.put_record("s1", review.round_record("s1", stage, plan, f"{stage}-hash{n}", found, f"h{n}"))
    return review.last(review.rounds_of(store.records("s1"), f"s1:{stage}"))


def proceed(store: FakeStore, stage: str, latest: Record, at: str) -> None:
    version = review.cap_version(latest, at)
    data = {"answer": review.PROCEED}
    store.put_record(
        "s1", Record(review.CAP_DECISION, review.cap_subject("s1", stage), version, review.PERSON + "s", data)
    )


def test_branch_cap_waits_for_the_person_at_the_checked_head():
    store = alpha()
    latest = rounds(store, "branch", raw(severity="P3"))
    store.put_record("s1", Record("checks", "s1", "h4", "agent:checks", {"head": "h4", "passed": True}))
    [item] = waiting(store)
    assert (item.kind, item.rank, item.at) == ("finding", 1, latest.at)
    assert item.next == ["decide context review-cap ACME-1 --stage branch"]
    proceed(store, "branch", latest, "h3")
    assert kinds(waiting(store)) == [("finding", 1, "ACME-1")]
    proceed(store, "branch", latest, "h4")
    assert waiting(store) == []


def test_plan_cap_waits_for_the_person_at_the_plan_version():
    store = alpha()
    latest = rounds(store, "plan")
    store.put_record("s1", Record(implplan.PURPOSE, "s1", "plan-v4", "agent:implplan", manifest()))
    [item] = waiting(store)
    assert (item.kind, item.next) == ("finding", ["decide context review-cap ACME-1 --stage plan"])
    proceed(store, "plan", latest, "plan-v4")
    assert waiting(store) == []


def test_finding_that_changes_delivery_waits_for_the_person():
    store = alpha()
    latest = rounds(store, "branch", raw(changes_delivery=True), count=1)
    [finding] = latest.data["findings"]
    [item] = waiting(store)
    assert (item.kind, item.rank, item.at) == ("finding", 1, latest.at)
    assert item.next == [f"decide context finding ACME-1 --finding {finding['id']}"]
    store.put_record("s1", asks.proposal_record(finding["id"], latest.version, "out of scope"))
    [item] = waiting(store)
    assert item.lines[-1] == "the hand proposes to decline it: out of scope"


def test_prompt_shows_the_latest_gap():
    store = alpha()
    store.put_record("s1", gaps.record("s1", "PermissionRequest", "Bash", "npm ci", "not allowed"))
    store.put_record("s1", gaps.record("s1", "PermissionRequest", "Bash", "npm test", "not allowed"))
    [gap] = [r for r in store.records("s1") if r.data.get("command") == "npm test"]
    blocked = hand("ACME-1", "blocked", "waiting", fleet.PERMISSION, iso(1795000000500))
    [item] = waiting(store, sessions=(blocked,))
    assert (item.kind, item.at, item.lines[-1]) == ("prompt", gap.at, "Bash: npm test")


def test_spike_hand_on_a_prompt_is_shown(state: Path):
    store, project, key = spiked(state)
    blocked = Session(
        "id-s", fleet.spike_name(key), "sid", "/w", iso(1795000000000), "blocked", "waiting", fleet.PERMISSION
    )
    found = [i for i in waiting(store, me=OTHER, sessions=(blocked,)) if i.kind == "prompt"]
    assert [(i.rank, i.ref, i.next) for i in found] == [(4, key, ["claude attach id-s"])]


def test_count_line():
    store = alpha()
    asked(store, "s1")
    summarized(store, "s2", "c2")
    assert queue.count_line(waiting(store)) == "2 waiting, top: sign-off ACME-2"
    assert queue.count_line([]) == "nothing waiting"
