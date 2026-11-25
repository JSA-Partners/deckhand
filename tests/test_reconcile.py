from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from deckhand import asks, config, features, fleet, queue, stalls, wakes
from deckhand.actions import Action, Adopt, Launch, Post, Remove, Resume, Stop
from deckhand.idle import IDLE_AFTER
from deckhand.policy import Policy
from deckhand.reading import Reading
from deckhand.reconcile import Facts, reconcile, reserved
from deckhand.records import HAND, Record, iso
from deckhand.store.fake import FakeStore
from deckhand.store.state import HandRow, Message
from tests.shell import Shell, row
from tests.test_feature_approval import P1, approve_everything, reapprove, setup
from tests.test_queue import asked
from tests.test_step_hand import ME, building, clone, configure, decided, spike_row, state
from tests.test_step_stories import setup as empty_feature

__all__ = ["clone", "state"]

T = 1795000000.0
BEFORE, AFTER = "2000-01-01T00:00:00.000Z", "2999-01-01T00:00:00.000Z"


def at(seconds: float) -> str:
    return iso(int(seconds * 1000))


def alone() -> FakeStore:
    store = building()
    store.assign("s2", "someone-else")
    return store


def hand_row(
    ref: str = "ACME-1",
    kind: str = queue.STORY_HAND,
    short: str | None = None,
    session: str | None = None,
    restarts: int = 0,
    wanted: str = BEFORE,
    issue: str = "s1",
    turn_ended: str | None = None,
) -> HandRow:
    cwd = f"/w/{ref.lower()}"
    return HandRow(fleet.hand_name(ref), ref, issue, kind, cwd, wanted, short, session, turn_ended, restarts)


def held(ref: str = "ACME-1", **changes: object) -> HandRow:
    return hand_row(ref, **({"short": f"id-{ref}", "session": f"sid-{ref}"} | changes))


def mail(seq: int = 1, written: float = T, delivered: str | None = None, hand: str = "hand-ACME-1") -> Message:
    return Message(seq, f"wake:s1:n{seq}", hand, asks.ANSWER, f"body {seq}", at(written), delivered, None)


def facts(
    store: FakeStore,
    *sessions: dict,
    hands: tuple[HandRow, ...] = (),
    pending: tuple[Message, ...] = (),
    now: float = T,
    woke: float = 0.0,
    cap: int = 1,
    me: str | None = ME,
    **rest: object,
) -> Facts:
    reading = Reading("ACME", features.current(store, "ACME"), me, [], {}, [])
    if "labels" not in rest:
        rest["labels"] = {"ACME": store.repository_labels("ACME")}
    if "clones" not in rest:
        clones = config.local().clones
        rest |= {"clones": clones, "policies": {path: Policy.load(path) for path in clones.values()}}
    return Facts(
        now=now,
        woke=woke,
        read=fleet.sessions(Shell(*sessions)),
        hands={h.name: h for h in hands},
        pending=list(pending),
        reading=reading,
        cap=cap,
        **rest,
    )


def story(store: FakeStore, ref: str) -> dict:
    feature = features.current(store, "ACME")
    assert feature is not None
    return next(s for s in feature.live if s["identifier"] == ref)


def started(store: FakeStore, issue: str = "s1", kind: str = queue.STORY_HAND) -> None:
    data = {"kind": kind, "session": fleet.hand_name(store.issue(issue)["identifier"])}
    store.put_record(issue, Record(asks.HAND_START, issue, "", HAND, data))


def launch(store: FakeStore, clone: Path, ref: str = "ACME-1", restart: bool = False) -> Launch:
    found = story(store, ref)
    return Launch(fleet.hand_name(ref), ref, queue.STORY_HAND, clone, found, ME, restart)


def test_a_failed_fleet_read_does_nothing(clone: Path):
    store = building()
    found = facts(store, cap=2)
    broken = Facts(found.now, found.woke, fleet.sessions(Shell(fleet_fails=True)), {}, [], found.reading, 2)
    assert broken.read.note
    assert reconcile(broken, store) == []


def test_a_free_slot_launches_the_next_story_in_build_order(clone: Path):
    store = building()
    assert reconcile(facts(store), store) == [launch(store, clone)]
    store.relate("s2", "s1")
    reapprove(store)
    assert reconcile(facts(store, cap=2), store) == [launch(store, clone, "ACME-2")]


def test_no_slot_no_launch(clone: Path):
    store = building()
    assert reconcile(facts(store, row("ACME-9", "working", "busy")), store) == []


def test_a_story_in_progress_with_no_local_start_is_held_elsewhere(clone: Path):
    store = building()
    store.assign("s1", ME)
    store.set_issue_state("s1", "In Progress")
    assert reconcile(facts(store), store) == [launch(store, clone, "ACME-2")]


def test_a_busy_name_gets_no_action(clone: Path):
    store = building()
    assert reconcile(facts(store, busy=frozenset({"hand-ACME-1"})), store) == []
    copies = (row("ACME-1", "working", "busy", at=1), row("ACME-1", "working", "busy") | {"id": "id-new"})
    assert reconcile(facts(store, *copies, busy=frozenset({"hand-ACME-1"})), store) == []


def test_a_wanted_row_with_a_live_session_is_adopted_not_launched(clone: Path):
    store = building()
    live = row("ACME-1", "working", "busy")
    expected = [Adopt("hand-ACME-1", "id-ACME-1", "sid-ACME-1")]
    assert reconcile(facts(store, live, hands=(hand_row(),)), store) == expected
    assert reconcile(facts(store, live, hands=(hand_row(short="id-ACME-1"),)), store) == expected


def test_a_copy_under_one_name_is_removed_and_the_recorded_one_kept(clone: Path):
    store = building()
    old = row("ACME-1", "working", "busy", at=1795000000000)
    new = row("ACME-1", "working", "busy", at=1795000060000) | {"id": "id-new", "sessionId": "sid-new"}
    assert reconcile(facts(store, old, new, hands=(held(),)), store) == [Remove("hand-ACME-1", "id-new")]
    unrecorded = facts(store, old, new, hands=(held(session="sid-other"),))
    assert reconcile(unrecorded, store) == [Remove("hand-ACME-1", "id-ACME-1")]


def test_an_answer_posts_one_keyed_message_with_its_wake_record(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    decided(store, "s1", "n1")
    records = store.records("s1")
    due = next(r for r in records if r.kind == asks.ANSWER)
    record = Record(asks.WAKE, "s1", "n1", HAND, {"decision": asks.ANSWER})
    body = wakes.body(story(store, "ACME-1"), due, records)
    found = reconcile(facts(store, row("ACME-1", "working", "busy"), hands=(held(),)), store)
    assert found == [Post("hand-ACME-1", "wake:s1:n1", asks.ANSWER, body, "s1", record)]
    assert found[0].record is not None and found[0].record.kind == asks.WAKE


def merged_with_two_open(clone: Path) -> FakeStore:
    store = building()
    store.add_repository_label("ACME", "widgets")
    store.create_issue("s3", "ACME", "Refunds", "Outcome: a refund is paid.", P1, ["widgets"])
    store.set_issue_state("s1", "Done")
    return store


def test_a_sibling_merge_posts_sync_to_each_open_hand_once_keyed(clone: Path):
    store = merged_with_two_open(clone)
    sessions = (row("ACME-2", "working", "busy"), row("ACME-3", "working", "busy"))
    hands = (held("ACME-2", issue="s2"), held("ACME-3", issue="s3"))
    body = wakes.sync_body(story(store, "ACME-1"))
    expected = [
        Post("hand-ACME-2", "sync:s1:hand-ACME-2", wakes.SYNC, body, "s2", None),
        Post("hand-ACME-3", "sync:s1:hand-ACME-3", wakes.SYNC, body, "s3", None),
    ]
    assert reconcile(facts(store, *sessions, hands=hands, cap=2), store) == expected


def test_a_sync_already_posted_is_not_posted_again(clone: Path):
    store = merged_with_two_open(clone)
    sessions = (row("ACME-2", "working", "busy"), row("ACME-3", "working", "busy"))
    hands = (held("ACME-2", issue="s2"), held("ACME-3", issue="s3"))
    posted = frozenset({"sync:s1:hand-ACME-2"})
    found = reconcile(facts(store, *sessions, hands=hands, cap=2, posted=posted), store)
    assert [a.key for a in found if isinstance(a, Post)] == ["sync:s1:hand-ACME-3"]


def test_a_hand_started_after_the_merge_gets_no_sync(clone: Path):
    store = merged_with_two_open(clone)
    sessions = (row("ACME-2", "working", "busy"), row("ACME-3", "working", "busy"))
    hands = (held("ACME-2", issue="s2", wanted=AFTER), held("ACME-3", issue="s3", kind=queue.DRAFT_HAND))
    assert reconcile(facts(store, *sessions, hands=hands, cap=2), store) == []


def test_a_stopped_hand_with_mail_is_resumed_with_the_text(clone: Path):
    store = alone()
    m = mail()
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(m,)), store)
    assert found == [resume(store, [m])]


def test_a_stopped_hand_with_mail_waits_for_a_slot(clone: Path):
    store = alone()
    sessions = (row("ACME-1", "stopped"), row("ACME-9", "working", "busy"))
    assert reconcile(facts(store, *sessions, hands=(held(),), pending=(mail(),)), store) == []


def test_an_alive_hand_with_fresh_mail_is_left_to_its_watcher(clone: Path):
    store = alone()
    idle = row("ACME-1", "idle", "idle")
    assert reconcile(facts(store, idle, hands=(held(),), pending=(mail(),), now=T + 10), store) == []


def test_an_idle_alive_hand_deaf_for_two_minutes_is_stopped(clone: Path):
    store = alone()
    idle = row("ACME-1", "idle", "idle")
    found = reconcile(facts(store, idle, hands=(held(),), pending=(mail(),), now=T + 121), store)
    assert found == [Stop("hand-ACME-1", "id-ACME-1", "unheard", (1,))]
    seen = mail(delivered=at(T + 5))
    assert reconcile(facts(store, idle, hands=(held(),), pending=(seen,), now=T + 121), store) == []


def test_a_working_hand_or_one_at_a_native_prompt_is_never_stopped_as_deaf(clone: Path):
    store = alone()
    for session in (row("ACME-1", "working", "busy"), row("ACME-1", "idle", "idle") | {"waitingFor": "input needed"}):
        assert reconcile(facts(store, session, hands=(held(),), pending=(mail(),), now=T + 600), store) == []


def test_a_due_story_with_unacknowledged_mail_is_not_nudged(clone: Path):
    store = alone()
    started(store)
    seen = mail(delivered=at(T))
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(seen,)), store)
    assert [type(action) for action in found] == [Resume]


def test_a_resume_carries_mail_delivered_but_not_acknowledged(clone: Path):
    store = alone()
    started(store)
    seen, fresh = mail(1, delivered=at(T)), mail(2)
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(seen, fresh)), store)
    assert found == [resume(store, [seen, fresh])]


def test_mail_for_a_stopped_hand_wins_the_last_slot_over_a_new_story(clone: Path):
    store = building()
    m = mail()
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(m,), cap=1), store)
    assert found == [resume(store, [m])]
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(m,), cap=2), store)
    assert found == [resume(store, [m]), launch(store, clone, "ACME-2")]


def test_reconcile_works_from_facts_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def unread(*_: object) -> None:
        raise AssertionError("reconcile read a file")

    monkeypatch.setattr(config, "local", unread)
    monkeypatch.setattr(Policy, "load", unread)
    store = alone()
    clone = tmp_path / "nowhere"
    known = {"clones": {"widgets": clone}, "policies": {clone: Policy("widgets")}}
    assert reconcile(facts(store, **known), store) == [launch(store, clone)]
    started(store)
    assert reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), **known), store) == [nudge(store)]


def test_a_session_the_fleet_no_longer_lists_is_launched_anew(clone: Path):
    store = alone()
    started(store)
    found = reconcile(facts(store, hands=(held(session="sid-gone"),)), store)
    assert found == [launch(store, clone, restart=True)]


def test_a_wake_from_sleep_restarts_the_deaf_clock(clone: Path):
    store = alone()
    idle = row("ACME-1", "idle", "idle")
    woken = facts(store, idle, hands=(held(),), pending=(mail(),), woke=T + 3600, now=T + 3610)
    assert reconcile(woken, store) == []


def fresh_start(store: FakeStore) -> Record:
    newest = max((r.at for r in store.records("s1")), default="")
    return Record(asks.HAND_START, "s1", newest, HAND, {"kind": queue.STORY_HAND, "session": "hand-ACME-1"})


def resume(store: FakeStore, messages: list[Message]) -> Resume:
    text, seqs = wakes.prompt(messages), tuple(m.seq for m in messages)
    return Resume("hand-ACME-1", "sid-ACME-1", "/w/acme-1", text, seqs, fresh_start(store), restart=True)


def test_a_first_stop_with_unacknowledged_mail_is_resumed_once_as_its_restart(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    m = mail()
    stopped = row("ACME-1", "stopped")
    [found] = reconcile(facts(store, stopped, hands=(held(),), pending=(m,)), store)
    assert found == resume(store, [m])
    assert isinstance(found, Resume) and found.record is not None
    store.put_record("s1", found.record)
    seen = mail(delivered=at(T))
    assert reconcile(facts(store, stopped, hands=(held(restarts=1),), pending=(seen,)), store) == []
    feature = features.current(store, "ACME")
    read = fleet.sessions(Shell(stopped))
    waiting = queue.items(store, "ACME", feature, ME, read, lambda _: Policy("widgets"), restarts={"ACME-1": 1})
    assert [(item.kind, item.ref) for item in waiting] == [(stalls.STALLED, "ACME-1")]


def nudge(store: FakeStore) -> Resume:
    return Resume("hand-ACME-1", "sid-ACME-1", "/w/acme-1", wakes.nudge_body(), (), fresh_start(store), restart=True)


def acted(store: FakeStore, action: Action, hand: HandRow) -> HandRow:
    if isinstance(action, Resume) and action.record is not None:
        store.put_record("s1", action.record)
    return replace(hand, restarts=hand.restarts + 1) if getattr(action, "restart", False) else hand


def policy(_: dict) -> Policy:
    return Policy("widgets")


def stalled_items(store: FakeStore, session: dict, restarts: int) -> list[tuple[str, str]]:
    feature = features.current(store, "ACME")
    read = fleet.sessions(Shell(session))
    found = queue.items(store, "ACME", feature, ME, read, policy, restarts={"ACME-1": restarts})
    return [(item.kind, item.ref) for item in found]


def test_a_first_stop_resumes_once_across_ticks_then_asks(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    stopped, working = row("ACME-1", "stopped"), row("ACME-1", "working", "busy")
    [first] = reconcile(facts(store, stopped, hands=(held(),)), store)
    assert first == nudge(store)
    hand = acted(store, first, held())
    assert hand.restarts == 1
    assert reconcile(facts(store, working, hands=(hand,)), store) == []
    assert reconcile(facts(store, stopped, hands=(hand,)), store) == []
    assert stalled_items(store, stopped, hand.restarts) == [(stalls.STALLED, "ACME-1")]


def test_a_restart_answer_resumes_once_across_ticks_then_asks_again(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    stopped, working = row("ACME-1", "stopped"), row("ACME-1", "working", "busy")
    hand = held(restarts=1)
    assert reconcile(facts(store, stopped, hands=(hand,)), store) == []
    assert stalled_items(store, stopped, 1) == [(stalls.STALLED, "ACME-1")]
    start = queue.last_start(store.records("s1"))
    assert start is not None
    store.put_record("s1", Record(stalls.STALLED, "s1", start.at, "person:session-1", {"answer": stalls.RESTART}))
    assert stalled_items(store, stopped, 1) == []
    [again] = reconcile(facts(store, stopped, hands=(hand,)), store)
    assert again == nudge(store)
    hand = acted(store, again, hand)
    assert reconcile(facts(store, working, hands=(hand,)), store) == []
    assert reconcile(facts(store, stopped, hands=(hand,)), store) == []
    assert stalled_items(store, stopped, hand.restarts) == [(stalls.STALLED, "ACME-1")]


def test_a_relaunched_session_is_adopted_over_the_recorded_one(clone: Path):
    store = alone()
    started(store)
    live = row("ACME-1", "working", "busy") | {"id": "id-new", "sessionId": "sid-new"}
    found = reconcile(facts(store, live, hands=(held(short="id-new", session="sid-gone"),)), store)
    assert found == [Adopt("hand-ACME-1", "id-new", "sid-new")]


def test_a_draft_session_is_never_lent_to_a_story_hand_with_mail(clone: Path):
    store = alone()
    started(store, kind=queue.DRAFT_HAND)
    found = reconcile(facts(store, row("ACME-1", "done"), hands=(hand_row(),), pending=(mail(),)), store)
    assert found == [launch(store, clone)]


def test_a_finished_hand_with_mail_is_never_resumed(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    store.set_issue_state("s1", "Done")
    assert reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),), pending=(mail(),)), store) == []


def test_a_written_draft_with_mail_is_never_resumed(clone: Path):
    store = setup()
    store.assign("s2", "someone-else")
    started(store, kind=queue.DRAFT_HAND)
    store.put_record("s1", Record(queue.DRAFT, "s1", "v1", HAND, {}))
    hand = held(kind=queue.DRAFT_HAND)
    assert reconcile(facts(store, row("ACME-1", "stopped"), hands=(hand,), pending=(mail(),)), store) == []


def test_a_busy_hand_holds_its_slot_until_the_fleet_shows_it(clone: Path):
    store = building()
    started(store)
    busy = frozenset({"hand-ACME-1"})
    stopped, working = row("ACME-1", "stopped"), row("ACME-1", "working", "busy")
    hand = held(restarts=1)
    assert reconcile(facts(store, stopped, hands=(hand,)), store) == [launch(store, clone, "ACME-2")]
    assert reconcile(facts(store, stopped, hands=(hand,), busy=busy), store) == []
    found = reconcile(facts(store, working, hands=(hand,), busy=busy, cap=2), store)
    assert found == [launch(store, clone, "ACME-2")]


def test_an_unheard_stop_and_its_resume_are_not_the_stops_restart(clone: Path):
    store = alone()
    started(store)
    sync = Message(1, "sync:s2:hand-ACME-1", "hand-ACME-1", wakes.SYNC, "ACME-2 merged.", at(T), None, None)
    hand, heard = held(restarts=1), frozenset({1})
    idle, stopped = row("ACME-1", "idle", "idle"), row("ACME-1", "stopped")
    found = reconcile(facts(store, idle, hands=(hand,), pending=(sync,), now=T + 121), store)
    assert found == [Stop("hand-ACME-1", "id-ACME-1", "unheard", (1,))]
    found = reconcile(facts(store, stopped, hands=(hand,), pending=(sync,), now=T + 130, unheard=heard), store)
    assert found == [Resume("hand-ACME-1", "sid-ACME-1", "/w/acme-1", wakes.prompt([sync]), (1,))]
    assert reconcile(facts(store, idle, hands=(hand,), pending=(sync,), now=T + 600, unheard=heard), store) == []


def test_the_deaf_clock_runs_from_the_last_turn_end(clone: Path):
    store = alone()
    idle = row("ACME-1", "idle", "idle")
    hand = held(turn_ended=at(T + 100))
    assert reconcile(facts(store, idle, hands=(hand,), pending=(mail(),), now=T + 150), store) == []
    found = reconcile(facts(store, idle, hands=(hand,), pending=(mail(),), now=T + 221), store)
    assert found == [Stop("hand-ACME-1", "id-ACME-1", "unheard", (1,))]


def test_a_spike_row_with_a_shallow_cwd_is_skipped(clone: Path):
    store = empty_feature()
    spike = HandRow("spike-k", "k", P1, "spike", "/w", BEFORE, None, None, None, 0)
    assert reconcile(facts(store, hands=(spike,)), store) == []


def test_a_due_story_with_a_known_session_is_nudged_with_a_fresh_start_record(clone: Path):
    store = alone()
    started(store)
    found = reconcile(facts(store, row("ACME-1", "stopped"), hands=(held(),)), store)
    assert found == [nudge(store)]
    assert found[0].record is not None and found[0].record.version == nudge(store).record.version


def test_a_finished_story_stops_its_idle_hand(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    store.set_issue_state("s1", "Done")
    idle = row("ACME-1", "idle", "idle")
    assert reconcile(facts(store, idle, hands=(held(),)), store) == [Stop("hand-ACME-1", "id-ACME-1", "finished")]
    late = facts(store, idle, hands=(held(),), pending=(mail(),), now=T + 600)
    assert reconcile(late, store) == [Stop("hand-ACME-1", "id-ACME-1", "finished")]
    assert reconcile(facts(store, row("ACME-1", "working", "busy"), hands=(held(),)), store) == []


def test_a_written_draft_stops_its_idle_hand(clone: Path):
    store = setup()
    store.assign("s2", "someone-else")
    started(store, kind=queue.DRAFT_HAND)
    store.put_record("s1", Record(queue.DRAFT, "s1", "v1", HAND, {}))
    idle = row("ACME-1", "idle", "idle")
    found = reconcile(facts(store, idle, hands=(held(kind=queue.DRAFT_HAND),)), store)
    assert found == [Stop("hand-ACME-1", "id-ACME-1", "finished")]


def test_a_written_draft_stops_its_hand_reported_working_then_the_story_hand_starts(clone: Path):
    store = setup()
    store.assign("s2", "someone-else")
    started(store, kind=queue.DRAFT_HAND)
    store.put_record("s1", Record(queue.DRAFT, "s1", "v1", HAND, {}))
    hand = held(kind=queue.DRAFT_HAND, turn_ended=at(T + 30))
    working, now = row("ACME-1", "working", "busy"), T + 30 + IDLE_AFTER + 1
    stop = Stop("hand-ACME-1", "id-ACME-1", "finished")
    assert reconcile(facts(store, working, hands=(hand,), now=now), store) == [stop]
    store.put_record("s1", Record(queue.TAKEN, "s1", "v1", "agent:captain", {}))
    approve_everything(store)
    assert reconcile(facts(store, working, hands=(hand,), now=now), store) == [stop]
    assert reconcile(facts(store, row("ACME-1", "stopped"), hands=(hand,), now=now), store) == [launch(store, clone)]


def test_a_working_draft_hand_stops_once_its_draft_is_saved_and_not_before(clone: Path):
    store = setup()
    store.assign("s2", "someone-else")
    started(store, kind=queue.DRAFT_HAND)
    working, hand = row("ACME-1", "working", "busy"), held(kind=queue.DRAFT_HAND)
    assert reconcile(facts(store, working, hands=(hand,)), store) == []
    store.put_record("s1", Record(queue.DRAFT, "s1", "v1", HAND, {}))
    assert reconcile(facts(store, working, hands=(hand,)), store) == [Stop("hand-ACME-1", "id-ACME-1", "finished")]


def test_a_stopped_session_of_another_kind_is_not_adopted(clone: Path):
    store = alone()
    started(store, kind=queue.DRAFT_HAND)
    found = reconcile(facts(store, row("ACME-1", "done"), hands=(hand_row(),)), store)
    assert found == [launch(store, clone)]
    found = reconcile(facts(store, row("ACME-1", "done"), hands=(hand_row(short="id-ACME-1"),)), store)
    assert found == [launch(store, clone)]


def test_a_failed_launch_waits_out_its_backoff(clone: Path):
    store = alone()
    failed = {"hand-ACME-1": (2, T)}
    assert reconcile(facts(store, now=T + 119, failed=failed), store) == []
    assert reconcile(facts(store, now=T + 121, failed=failed), store) == [launch(store, clone)]
    assert reconcile(facts(store, now=T + 900, failed={"hand-ACME-1": (9, T)}), store) == [launch(store, clone)]


def test_a_wanted_spike_is_launched_within_the_slot_cap(clone: Path):
    store = empty_feature()
    key = "a1b2c3d4e5f6"
    name = fleet.spike_name(key)
    spike = HandRow(name, key, P1, "spike", str(clone / ".worktrees" / name), BEFORE, None, None, None, 0)
    expected = [Launch(name, key, "spike", clone, None, ME)]
    assert reconcile(facts(store, hands=(spike,)), store) == expected
    assert reconcile(facts(store, row("ACME-9", "working", "busy"), hands=(spike,)), store) == []
    assert reconcile(facts(store, spike_row(key, "working", "busy"), hands=(spike,), cap=2), store) == [
        Adopt(name, "id-x", "sid-x")
    ]


def test_a_wanted_spike_with_a_stopped_session_restarts(clone: Path):
    store = empty_feature()
    key = "a1b2c3d4e5f6"
    name = fleet.spike_name(key)
    spike = HandRow(name, key, P1, "spike", str(clone / ".worktrees" / name), BEFORE, None, None, None, 0)
    found = reconcile(facts(store, spike_row(key, "stopped"), hands=(spike,)), store)
    assert found == [Launch(name, key, "spike", clone, None, ME, restart=True)]


def test_a_first_stop_is_restarted_once_with_the_restart_flag(clone: Path):
    store = alone()
    started(store)
    stopped = row("ACME-1", "stopped")
    assert reconcile(facts(store, stopped, hands=(held(),)), store) == [nudge(store)]
    assert reconcile(facts(store, hands=(held(),)), store) == [launch(store, clone, restart=True)]


def test_a_second_stop_without_progress_gets_no_restart(clone: Path):
    store = alone()
    started(store)
    stopped = row("ACME-1", "stopped")
    assert reconcile(facts(store, stopped, hands=(held(restarts=1),)), store) == []
    assert reconcile(facts(store, hands=(held(restarts=1),)), store) == []


def test_a_first_launch_is_not_a_restart(clone: Path):
    store = alone()
    [found] = reconcile(facts(store), store)
    assert isinstance(found, Launch) and found.restart is False


def test_without_a_personal_key_only_copies_and_adoptions_happen(clone: Path):
    configure(clone, 3)
    store = alone()
    started(store)
    old = row("ACME-2", "working", "busy", at=1795000000000)
    new = row("ACME-2", "working", "busy", at=1795000060000) | {"id": "id-new", "sessionId": "sid-new"}
    sessions = (old, new, row("ACME-1", "stopped"), row("ACME-3", "idle", "idle"))
    hands = (held(), held("ACME-2", issue="s2"), hand_row("ACME-3", issue="s3"))
    expected = [Remove("hand-ACME-2", "id-new"), Adopt("hand-ACME-3", "id-ACME-3", "sid-ACME-3")]
    assert reconcile(facts(store, *sessions, hands=hands, pending=(mail(),), cap=3, me=None), store) == expected
    found = facts(store, *sessions, hands=hands, cap=3)
    unread = Facts(found.now, found.woke, found.read, found.hands, [mail()], None, 3)
    assert reconcile(unread, store) == expected


def test_a_hand_stopped_as_unheard_keeps_its_slot_until_its_resume(clone: Path):
    store = building()
    store.assign("s1", ME)
    started(store)
    sync = Message(1, "sync:s3:hand-ACME-1", "hand-ACME-1", wakes.SYNC, "ACME-3 merged.", at(T), None, None)
    hand, heard = held(restarts=1), frozenset({1})
    idle, stopped = row("ACME-1", "idle", "idle"), row("ACME-1", "stopped")
    found = reconcile(facts(store, idle, hands=(hand,), pending=(sync,), now=T + 121), store)
    assert found == [Stop("hand-ACME-1", "id-ACME-1", "unheard", (1,))]
    found = reconcile(facts(store, stopped, hands=(hand,), pending=(sync,), now=T + 125, unheard=heard), store)
    assert found == [Resume("hand-ACME-1", "sid-ACME-1", "/w/acme-1", wakes.prompt([sync]), (1,))]
    assert stalled_items(store, stopped, 1) == [(stalls.STALLED, "ACME-1")]
    failed = {"hand-ACME-1": (1, T + 125)}
    waits = facts(store, stopped, hands=(hand,), pending=(sync,), now=T + 130, unheard=heard, failed=failed)
    assert reconcile(waits, store) == []
    feature = features.current(store, "ACME")
    read = fleet.sessions(Shell(stopped))
    quiet = frozenset({"ACME-1"})
    waiting = queue.items(store, "ACME", feature, ME, read, policy, quiet=quiet, restarts={"ACME-1": 1})
    assert waiting == []


def test_an_unheard_stop_waits_for_a_slot_to_resume_into(clone: Path):
    store = alone()
    started(store)
    sync = Message(1, "sync:s3:hand-ACME-1", "hand-ACME-1", wakes.SYNC, "ACME-3 merged.", at(T), None, None)
    sessions = (row("ACME-1", "idle", "idle"), row("ACME-9", "working", "busy"))
    assert reconcile(facts(store, *sessions, hands=(held(),), pending=(sync,), now=T + 121), store) == []


def unheard_hand() -> tuple[FakeStore, Message, HandRow]:
    store = building()
    store.assign("s1", ME)
    started(store)
    sync = Message(1, "sync:s3:hand-ACME-1", "hand-ACME-1", wakes.SYNC, "ACME-3 merged.", at(T), None, None)
    return store, sync, held(restarts=1)


def after_stop(store: FakeStore, *sessions: dict, hand: HandRow, sync: Message, **rest: object) -> Facts:
    return facts(store, *sessions, hands=(hand,), pending=(sync,), now=T + 130, unheard=frozenset({1}), **rest)


def test_a_reservation_holds_only_while_a_resume_is_possible(clone: Path):
    store, sync, hand = unheard_hand()
    stopped = after_stop(store, row("ACME-1", "stopped"), hand=hand, sync=sync)
    assert reserved(stopped) == frozenset({"hand-ACME-1"})
    gone = after_stop(store, hand=hand, sync=sync)
    assert reserved(gone) == frozenset()
    assert reconcile(gone, store) == [launch(store, clone, "ACME-2")]


def test_a_hand_alive_again_with_its_mail_undelivered_holds_no_reservation(clone: Path):
    store, sync, hand = unheard_hand()
    again = after_stop(store, row("ACME-1", "idle", "idle"), hand=hand, sync=sync)
    assert reserved(again) == frozenset()
    assert reconcile(again, store) == [launch(store, clone, "ACME-2")]


def test_a_story_finished_while_its_hand_was_stopped_holds_no_reservation(clone: Path):
    store, sync, hand = unheard_hand()
    store.set_issue_state("s1", "Done")
    done = after_stop(store, row("ACME-1", "stopped"), hand=hand, sync=sync)
    assert reserved(done) == frozenset()
    assert reconcile(done, store) == [launch(store, clone, "ACME-2")]


def test_an_unlisted_short_adopts_the_one_alive_session_of_its_name(clone: Path):
    store = building()
    running = row("ACME-1", "working", "busy") | {"id": "id-new", "sessionId": "sid-new"}
    assert reconcile(facts(store, running, hands=(held(),)), store) == [Adopt("hand-ACME-1", "id-new", "sid-new")]
    other = running | {"id": "id-other", "sessionId": "sid-other", "startedAt": 1795000060000}
    found = reconcile(facts(store, running, other, hands=(held(),)), store)
    assert found == [Remove("hand-ACME-1", "id-new"), Adopt("hand-ACME-1", "id-other", "sid-other")]


def test_a_stopped_unrecorded_copy_is_removed_without_a_stop_and_holds_no_slot(clone: Path):
    store = building()
    original = row("ACME-1", "working", "busy", at=1795000000000)
    copy = row("ACME-1", "stopped", at=1795000060000) | {"id": "copy", "sessionId": "sid-copy"}
    assert reconcile(facts(store, original, copy, hands=(held(),)), store) == [
        Remove("hand-ACME-1", "copy", stop=False)
    ]


def test_a_stopped_session_is_kept_when_the_recorded_one_is_not_listed(clone: Path):
    store = building()
    stopped = row("ACME-1", "stopped") | {"id": "old", "sessionId": "sid-old"}
    found = reconcile(facts(store, stopped, hands=(held(session="sid-gone"),)), store)
    assert not [a for a in found if isinstance(a, Remove)]


def test_the_sync_reads_repository_labels_from_the_facts(clone: Path):
    store = alone()
    store.assign("s2", ME)
    store.set_issue_state("s1", "Done")
    found = reconcile(facts(store, hands=(held("ACME-2", issue="s2"),), labels={}), store)
    assert not [a for a in found if isinstance(a, Post)]


ENDED = at(T - 200)


def idle_facts(store: FakeStore, hand: HandRow, **rest: object) -> Facts:
    blocked = row("ACME-1", "blocked", "busy")
    return facts(store, blocked, hands=(hand,), idle=frozenset({"hand-ACME-1"}), **rest)


def test_an_idle_hand_is_nudged_once_per_turn_end_as_its_restart(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    hand = held(turn_ended=ENDED)
    [nudge] = reconcile(idle_facts(store, hand), store)
    start = asks.hand_start(story(store, "ACME-1"), store.records("s1"), queue.STORY_HAND)
    assert nudge == Post("hand-ACME-1", f"nudge:s1:{ENDED}", wakes.NUDGE, wakes.nudge_body(), "s1", start, True)


def test_no_nudge_while_linear_is_unreadable(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    assert reconcile(idle_facts(store, held(turn_ended=ENDED), readable=False), store) == []


def test_an_idle_hand_past_its_restart_waits_for_the_person(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    assert reconcile(idle_facts(store, held(turn_ended=ENDED, restarts=1)), store) == []


def test_an_answered_idle_hand_is_stopped_for_the_restart_or_to_stay_stopped(clone: Path):
    for answer in (stalls.RESTART, stalls.LEAVE):
        store = alone()
        store.assign("s1", ME)
        started(store)
        start = queue.last_start(store.records("s1"), queue.STORY_HAND)
        assert start is not None
        store.put_record("s1", Record(stalls.STALLED, "s1", start.at, "person:session-1", {"answer": answer}))
        found = reconcile(idle_facts(store, held(turn_ended=ENDED, restarts=1)), store)
        assert found == [Stop("hand-ACME-1", "id-ACME-1", "idle")]


def test_an_idle_hand_waiting_on_the_person_is_left_alone(clone: Path):
    store = alone()
    store.assign("s1", ME)
    started(store)
    asked(store, "s1")
    assert reconcile(idle_facts(store, held(turn_ended=ENDED)), store) == []
