from __future__ import annotations

from dataclasses import replace

from deckhand import asks, checks, features, implplan, queue, review, signoff, stage
from deckhand.records import HAND, Record, iso
from deckhand.review import Finding, RoundPlan

STORY = {"id": "s1", "identifier": "ACME-1", "state": "In Progress"}
PERSON = review.PERSON + "session-1"
PLAN = "plan-v1"
HEAD = "a" * 40
MOVED = "b" * 40
P1 = Finding("correctness", "P1", "src/a.py", 3, "an order is lost", "queue it", "line 3", 0.9)
P2 = replace(P1, severity="P2", summary="a name is vague")


class Log:
    def __init__(self) -> None:
        self.records: list[Record] = []
        self.ms = 1795000000000

    def add(self, record: Record) -> Record:
        self.ms += 1000
        held = replace(record, at=iso(self.ms))
        self.records.append(held)
        return held

    def of(self, state: str = "In Progress") -> stage.Stage:
        return stage.of({**STORY, "state": state}, self.records)


def started(log: Log, kind: str = queue.STORY_HAND) -> Record:
    return log.add(Record(asks.HAND_START, "s1", "v", HAND, {"kind": kind, "session": "hand-ACME-1"}))


def drafted(log: Log) -> Record:
    return log.add(Record(queue.DRAFT, "s1", "d1", "agent:draft", {}))


def planned(log: Log, version: str = PLAN) -> Record:
    return log.add(Record(implplan.PURPOSE, "s1", version, "agent:implplan", {}))


def plan_round(log: Log, n: int, findings: list[Finding], target: str = PLAN) -> Record:
    plan = RoundPlan(n, scoped=n > 1, base_hash="")
    return log.add(review.round_record("s1", "plan", plan, target, findings))


def checked(log: Log, head: str = HEAD, passed: bool = True) -> Record:
    return log.add(Record(checks.KIND, "s1", head, "agent:checks", {"passed": passed}))


def branch_round(log: Log, n: int, findings: list[Finding], head: str = HEAD) -> Record:
    plan = RoundPlan(n, scoped=n > 1, base_hash="")
    return log.add(review.round_record("s1", "branch", plan, f"diff-{head}", findings, head))


def summarized(log: Log, head: str = HEAD) -> Record:
    return log.add(Record(signoff.SUMMARY, "s1", "digest", "agent:signoff", {"head": head}))


def signed(log: Log, head: str = HEAD) -> Record:
    data = {"answer": signoff.SIGN, "nonce": "n1", "by": "viewer-1"}
    return log.add(Record(signoff.KIND, "s1", f"{head}:digest:quiz", PERSON, data))


def cap_answer(log: Log, stage_name: str, latest: Record, answer: str) -> Record:
    subject = review.cap_subject("s1", stage_name)
    version = review.cap_version(latest)
    return log.add(Record(review.CAP_DECISION, subject, version, PERSON, {"answer": answer}))


def built(log: Log) -> Record:
    started(log)
    planned(log)
    return plan_round(log, 1, [])


def test_no_records_is_drafting_with_no_time():
    assert Log().of() == stage.Stage(stage.DRAFTING, None, None)


def test_a_draft_hand_is_drafting_since_it_started():
    log = Log()
    start = started(log, queue.DRAFT_HAND)
    assert log.of() == stage.Stage(stage.DRAFTING, None, start.at)


def test_a_written_draft_is_planning():
    log = Log()
    started(log, queue.DRAFT_HAND)
    draft = drafted(log)
    assert log.of() == stage.Stage(stage.PLANNING, None, draft.at)


def test_an_approved_story_is_planning():
    log = Log()
    approval = log.add(Record(features.APPROVAL, "s1", "f1", PERSON, {}))
    assert log.of() == stage.Stage(stage.PLANNING, None, approval.at)


def test_a_story_hand_is_planning_since_it_started():
    log = Log()
    drafted(log)
    start = started(log)
    planned(log)
    assert log.of() == stage.Stage(stage.PLANNING, None, start.at)


def test_a_plan_round_with_a_p1_is_plan_review_with_its_count():
    log = Log()
    started(log)
    planned(log)
    plan_round(log, 1, [P1])
    planned(log, "plan-v2")
    second = plan_round(log, 2, [P1], "plan-v2")
    assert log.of() == stage.Stage(stage.PLAN_REVIEW, 2, second.at)


def test_a_clean_plan_round_is_building_since_it_stopped():
    log = Log()
    started(log)
    planned(log)
    clean = plan_round(log, 1, [P2])
    assert log.of() == stage.Stage(stage.BUILDING, None, clean.at)


def test_a_plan_revised_after_a_clean_third_round_is_back_in_plan_review():
    log = Log()
    started(log)
    planned(log)
    plan_round(log, 1, [P1])
    plan_round(log, 2, [P1], "plan-v2")
    planned(log, "plan-v3")
    third = plan_round(log, 3, [P2], "plan-v3")
    assert log.of() == stage.Stage(stage.BUILDING, None, third.at)
    planned(log, "plan-v4")
    assert log.of() == stage.Stage(stage.PLAN_REVIEW, 3, third.at)


def test_a_fourth_branch_round_reads_its_number_alone():
    log = Log()
    built(log)
    for n, head in enumerate((HEAD, MOVED, "c" * 40), start=1):
        checked(log, head)
        branch_round(log, n, [P2], head)
    checked(log, "d" * 40)
    fourth = branch_round(log, 4, [P2], "d" * 40)
    found = log.of()
    assert found == stage.Stage(stage.BRANCH_REVIEW, 4, fourth.at)
    assert stage.as_data(found) == {"name": "branch review", "round": 4, "since": fourth.at}


def test_proceed_at_the_plan_cap_is_building_since_the_answer():
    log = Log()
    started(log)
    planned(log)
    plan_round(log, 1, [P1])
    plan_round(log, 2, [P1], "plan-v2")
    planned(log, "plan-v3")
    third = plan_round(log, 3, [P1], "plan-v3")
    proceed = cap_answer(log, "plan", third, review.PROCEED)
    assert log.of() == stage.Stage(stage.BUILDING, None, proceed.at)


def test_rework_at_the_plan_cap_returns_the_story_to_planning():
    log = Log()
    start = started(log)
    planned(log)
    plan_round(log, 1, [P1])
    plan_round(log, 2, [P1], "plan-v2")
    planned(log, "plan-v3")
    third = plan_round(log, 3, [P1], "plan-v3")
    cap_answer(log, "plan", third, review.REWORK)
    assert log.of() == stage.Stage(stage.PLANNING, None, start.at)


def test_a_checks_record_is_checks_since_it_ran():
    log = Log()
    built(log)
    ran = checked(log, passed=False)
    assert log.of() == stage.Stage(stage.CHECKS, None, ran.at)


def test_a_branch_round_is_branch_review_with_its_count():
    log = Log()
    built(log)
    checked(log)
    branch_round(log, 1, [P1])
    checked(log, MOVED)
    second = branch_round(log, 2, [], MOVED)
    assert log.of() == stage.Stage(stage.BRANCH_REVIEW, 2, second.at)


def test_rework_at_the_branch_cap_returns_the_story_to_checks():
    log = Log()
    built(log)
    checked(log)
    branch_round(log, 1, [P1])
    branch_round(log, 2, [P1], "c" * 40)
    third = branch_round(log, 3, [P1], "d" * 40)
    cap_answer(log, "branch", third, review.REWORK)
    ran = checked(log, "e" * 40)
    assert log.of() == stage.Stage(stage.CHECKS, None, ran.at)


def test_a_summary_at_the_checked_head_is_sign_off():
    log = Log()
    built(log)
    checked(log)
    branch_round(log, 1, [])
    summary = summarized(log)
    assert log.of() == stage.Stage(stage.SIGN_OFF, None, summary.at)


def test_a_summary_at_an_older_head_is_not_sign_off():
    log = Log()
    built(log)
    checked(log)
    branch_round(log, 1, [])
    summarized(log)
    checked(log, MOVED)
    assert log.of().name == stage.BRANCH_REVIEW


def test_a_sign_off_at_the_checked_head_is_merging():
    log = Log()
    built(log)
    checked(log)
    branch_round(log, 1, [])
    summarized(log)
    sign = signed(log)
    assert log.of() == stage.Stage(stage.MERGING, None, sign.at)


def test_a_void_sign_off_is_not_merging():
    log = Log()
    built(log)
    checked(log)
    branch_round(log, 1, [])
    summary = summarized(log)
    sign = signed(log)
    log.add(signoff.void_record("s1", sign, "checks failed: lint"))
    assert log.of() == stage.Stage(stage.SIGN_OFF, None, summary.at)


def test_a_sign_off_before_the_head_moved_is_not_merging():
    log = Log()
    built(log)
    checked(log)
    branch_round(log, 1, [])
    summarized(log)
    signed(log)
    checked(log, MOVED)
    assert log.of().name == stage.BRANCH_REVIEW


def test_an_answer_other_than_sign_off_is_not_merging():
    log = Log()
    built(log)
    checked(log)
    summary = summarized(log)
    log.add(Record(signoff.KIND, "s1", f"{HEAD}:digest:quiz", PERSON, {"answer": signoff.NOT_YET, "nonce": "n1"}))
    assert log.of() == stage.Stage(stage.SIGN_OFF, None, summary.at)


def test_a_done_story_is_merged_with_no_time():
    log = Log()
    built(log)
    checked(log)
    summarized(log)
    signed(log)
    assert log.of("Done") == stage.Stage(stage.MERGED, None, None)


def test_as_data_carries_the_round_alone():
    assert stage.as_data(stage.Stage(stage.PLAN_REVIEW, 2, "t")) == {"name": "plan review", "round": 2, "since": "t"}
    assert stage.as_data(stage.Stage(stage.BUILDING, None, "t")) == {"name": "building", "round": None, "since": "t"}
