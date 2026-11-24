from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand import asks, claims, features, planning, queue, signoff, spikes
from deckhand.features import FEATURE_LABEL, Phase
from deckhand.fleet import Fleet
from deckhand.policy import Policy
from deckhand.step import Refusal
from deckhand.steps.after import After
from deckhand.steps.ask import Ask
from deckhand.steps.build import Build
from deckhand.steps.captain import Captain
from deckhand.steps.checks import Checks
from deckhand.steps.decide import Decide
from deckhand.steps.hand import Hand
from deckhand.steps.implplan import ImplPlan
from deckhand.steps.merge import Merge
from deckhand.steps.quiz import Quiz
from deckhand.steps.review import Review
from deckhand.steps.signoff import Signoff
from deckhand.steps.spike import Spike
from deckhand.steps.stories import Stories
from deckhand.store.fake import FakeStore
from tests.shell import Shell, row
from tests.test_build_flow import World
from tests.test_feature_approval import P1, decided, reviewed, sat
from tests.test_implplan import BASE, Git, manifest
from tests.test_queue import Clock
from tests.test_quiz import SOURCE, item, reader, reply, summary
from tests.test_step_captain import DAY
from tests.test_step_checks import Commands
from tests.test_step_hand import ME, TEAM, clone, configure, spike_row, state
from tests.test_step_review import answer, record_round
from tests.test_step_signoff import Clock as MergeClock
from tests.test_step_stories import entry, story_id

SHOWN = "src/a.py:1"

__all__ = ["clone", "state"]

OTHER = "someone-else"
QUESTION = "Does the queue keep order?"
ASKED = {"question": "Which queue?", "options": ["Redis", "SQS"], "why": "Both fit."}
CHORES = ["drop the old table", "rotate the secret"]


def top(shown: list[str]) -> list[str]:
    return shown[len(shown) - shown[::-1].index("") :]


class Run:
    def __init__(self, tmp_path: Path, clone: Path, state: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.tmp, self.clone, self.state, self.capsys = tmp_path, clone, state, capsys
        self.store = FakeStore(now=Clock())
        self.store.add_repository_label("ACME", "widgets")
        self.store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
        self.store.set_project_dates(P1, "2026-11-16", "2026-11-23")
        feature = features.open_feature(self.store, "ACME")
        assert feature is not None
        for name in planning.SECTIONS:
            planning.save_section(self.store, feature, name, f"The {name} of Alpha.", False)
        self.shell = Shell()
        self.world = World()
        self.world.head = "c1"

    def out(self) -> list[str]:
        return self.capsys.readouterr().out.splitlines()

    def write(self, name: str, data: object) -> str:
        path = self.tmp / name
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return str(path)

    def captain(self, store: FakeStore | None = None, person: FakeStore | None = None) -> list[str]:
        store = store if store is not None else self.store
        self.capsys.readouterr()
        Captain(store, self.shell, person=person or store, today=lambda: DAY).context(TEAM)
        return self.out()

    def hand(self, *args: str) -> list[str]:
        self.capsys.readouterr()
        Hand(self.store, self.shell, person=self.store).apply([*args, *TEAM])
        return self.out()

    def refused(self, step: object, args: list[str], match: str) -> None:
        written = list(self.store.writes)
        with pytest.raises(Refusal, match=match):
            step.apply([*args, *TEAM])
        assert self.store.writes == written

    def approve(self, *stories: str) -> None:
        reviewed(self.store)
        decided(self.store, "contract", P1, "Approve", list(stories))
        decided(self.store, "invariants", P1, "Approve", list(stories))
        for story in stories:
            sat(self.store, story)
        decided(self.store, "feature-approval", P1, "Approve", list(stories))

    def decide(self) -> Decide:
        files = reader(self.clone / ".worktrees" / "acme-1-orders", {"src/a.py": SOURCE})
        return Decide(self.store, runner=self.world, repo=self.clone, reader=files)

    def build_to_sign_off(self, ref: str) -> None:
        plan, found = (
            self.write("plan.md", "# Plan\n\n- [ ] queue the order\n"),
            self.write("m.json", manifest(after_merge=CHORES)),
        )
        ImplPlan(self.store, Git(BASE), self.clone).apply([ref, *TEAM, "--plan", plan, "--manifest", found])
        record_round(Review(self.store, self.world, self.clone), self.tmp, ref=ref)
        self.world.head = "c2"
        Build(self.store, self.world, self.clone).apply([ref, "push", *TEAM])
        Checks(self.store, self.world, self.clone, Commands()).apply([ref, *TEAM])
        record_round(Review(self.store, self.world, self.clone), self.tmp, ref=ref, stage="branch")
        made = self.write("change.json", summary(1))
        Signoff(self.store, self.world, self.clone).apply([ref, "summary", "--summary", made, *TEAM])
        quizzed = self.write("quiz.json", [item(where=SHOWN)])
        Quiz(self.store, self.world, self.clone).apply([ref, *TEAM, "--quiz", quizzed, "--summary", made])

    def pass_quiz(self, ref: str) -> None:
        step = self.decide()
        self.capsys.readouterr()
        step.context(["quiz", ref, "--item", "1", *TEAM, "--json"])
        reply(self.state, step, json.loads(self.capsys.readouterr().out), "The idempotency key")


def test_a_feature_runs_from_spike_to_the_last_story(
    tmp_path: Path, clone: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    configure(clone, 2)
    run = Run(tmp_path, clone, state, capsys)
    store, shell = run.store, run.shell

    assert run.hand("spike", "--repository", "widgets", "--file", run.write("q.md", QUESTION)) == [
        f"started spike-{spikes.key(P1, QUESTION)}"
    ]
    key = spikes.key(P1, QUESTION)
    shell.rows = [spike_row(key, "working", "busy")]
    assert "slots 1 of 2" in run.captain()
    stories = [entry("a", title="Orders"), entry("b", title="Receipts", depends_on=["a"]), entry("c", title="Refunds")]
    story_map = run.write("map.json", stories)
    run.refused(Stories(store, shell), ["--file", story_map], f"spike {key} is still running")

    Spike(store).apply([key, "--file", run.write("answer.md", "Found: yes, per partition."), *TEAM])
    shell.rows = [spike_row(key, "done")]
    found = queue.items(store, "ACME", features.open_feature(store, "ACME"), ME, Fleet(), lambda _: Policy("widgets"))
    assert [(i.kind, i.lines) for i in found] == [
        ("planning", ["the feature has no stories"]),
        ("spike", [f"spike answered, read it: Spike: {QUESTION}"]),
    ]
    assert run.captain()[0].startswith("## ● 2 need you · oldest Alpha planning ")
    assert run.captain(person=FakeStore(viewer=OTHER))[0].startswith("## ● 2 need you · oldest Alpha planning ")

    Stories(store, shell).apply([*TEAM, "--file", story_map])
    assert run.captain()[0] == "## ● Alpha needs you"
    a, b, c = story_id("a"), story_id("b"), story_id("c")
    run.approve(a, b, c)
    assert features.phase(features.open_feature(store, "ACME")) is Phase.BUILDING
    assert "ACME-2 Backlog unassigned blocked by ACME-1" in run.captain()

    assert run.hand("fill") == ["started hand-ACME-1", "started hand-ACME-3", "no slot is free: 2 of 2 in use"]
    assert shell.launched() == ["spike-" + key, "hand-ACME-1", "hand-ACME-3"]
    (clone / ".worktrees" / "acme-1-orders").mkdir(parents=True)
    shell.rows = [row("ACME-1", "working", "busy"), row("ACME-3", "working", "busy")]

    Ask(store).apply(["ACME-3", "--file", run.write("ask.json", ASKED), *TEAM])
    shell.rows = [row("ACME-1", "working", "busy"), row("ACME-3", "blocked", "waiting")]
    shown = run.captain()
    assert (shown[0], top(shown)[0]) == ("## ● ACME-3 needs you", "ACME-3 Refunds")
    run.build_to_sign_off("ACME-1")
    shell.rows = [row("ACME-1", "blocked", "waiting"), row("ACME-3", "blocked", "waiting")]
    shown = run.captain()
    assert shown[0].startswith("## ● 2 need you")
    assert shown[2].startswith("**sign-off** ACME-1 at c2 · waiting ")
    assert top(shown)[:2] == ["ACME-1 Orders", "ready to sign off at c2"]
    assert "next: /deckhand:signoff ACME-1" in shown

    run.pass_quiz("ACME-1")
    Signoff(store, run.world, clone).apply(["ACME-1", "review", *TEAM])
    answer(state, run.decide(), capsys, ["sign-off", "ACME-1"], "Sign off")
    assert signoff.current(store.records(a), a, "c2")
    assert run.captain()[0] == "## ● ACME-3 needs you"
    answer(state, run.decide(), capsys, ["question", asks.question_id("Which queue?"), "--story", "ACME-3"], "SQS")
    shown = run.captain()
    assert ("nothing needs you" in shown[0], "wake due: ACME-1, ACME-3" in shown) == (True, True)

    assert run.hand("wake", "ACME-1")[0].startswith("send to hand-ACME-1: ")
    shell.rows = [row("ACME-1", "working", "busy"), row("ACME-3", "blocked", "waiting")]
    woken = run.hand("wake", "ACME-3")
    assert len(woken) == 1 and woken[0].startswith("send to hand-ACME-3: ")
    shell.rows = [row("ACME-1", "working", "busy"), row("ACME-3", "working", "busy")]
    assert not any(line.startswith("wake due") for line in run.captain())
    written = list(store.writes)
    assert run.hand("wake", "ACME-3") == ["nothing to wake"]
    assert store.writes == written

    Merge(store, run.world, clone, MergeClock().sleep, MergeClock()).apply(["ACME-1", *TEAM])
    assert run.world.ran("gh", "pr", "merge")[0][-2:] == ["--match-head-commit", "c2"]
    store.set_issue_state(a, "Done")
    After(store).apply(["ACME-1", "done", "1", "--note", "dropped in migration 12", *TEAM])
    After(store).apply(["ACME-1", "person", "2", "--why", "the secret lives in the vault", *TEAM])
    shell.rows = [row("ACME-1", "done"), row("ACME-3", "working", "busy")]
    shown = run.captain()
    assert (shown[0], top(shown)[:2]) == ("## ● ACME-1 needs you", ["ACME-1 Orders", "item 2: rotate the secret"])
    unclaimed = store.issue(b)
    assert run.hand("fill") == ["started hand-ACME-2", "no slot is free: 2 of 2 in use"]
    assert shell.launched()[3:] == ["hand-ACME-2"]

    shell.rows = [row("ACME-1", "done"), row("ACME-2", "working", "busy"), row("ACME-3", "working", "busy")]
    first = run.captain()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(store.snapshot()))
    assert run.captain(store=FakeStore.from_snapshot(snapshot)) == first
    assert first[0] == "## ● ACME-1 needs you"
    assert "nothing needs you" in run.captain(person=FakeStore(viewer=OTHER))[0]

    claims.claim(store, unclaimed, OTHER)
    assert (store.issue(b)["assignee"], store.issue(c)["assignee"]) == (OTHER, ME)
    run.refused(Ask(store), ["ACME-2", "--file", run.write("ask.json", ASKED)], f"ACME-2 belongs to {OTHER}")
