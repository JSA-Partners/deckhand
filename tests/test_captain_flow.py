from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from deckhand import answers, asks, fleet, linker, spikes, status, wakes
from deckhand.actions import Action
from deckhand.acts import Acts
from deckhand.engine import Engine
from deckhand.records import iso
from deckhand.steps.captain import Captain
from deckhand.steps.decide import Decide
from deckhand.steps.hand import Hand
from deckhand.steps.mail import Mail
from deckhand.store.fake import FakeStore
from deckhand.store.local import default_path
from deckhand.store.open import Stores
from deckhand.store.state import LocalState
from tests.shell import World
from tests.test_engine import Clock, FakePoller
from tests.test_feature_approval import P1
from tests.test_queue import asked
from tests.test_step_hand import ME, QUESTION, TEAM, building, clone, state
from tests.test_step_review import answer
from tests.test_step_stories import setup as empty_feature

__all__ = ["clone", "state"]

DAY = date(2026, 11, 17)
NAME = "hand-ACME-1"


class Now:
    def __init__(self, acts: Acts) -> None:
        self.acts, self.done = acts, []

    def submit(self, action: Action) -> None:
        self.done.append((action, self.acts.run(action)))

    def busy(self) -> frozenset[str]:
        return frozenset()

    def results(self) -> list[tuple[Action, str | None]]:
        found, self.done = self.done, []
        return found

    def stop(self) -> None:
        pass

    def working(self) -> bool:
        return False


class Flow:
    def __init__(self, store: FakeStore) -> None:
        self.store, self.world, self.clock = store, World(), Clock()
        self.state = LocalState(default_path(), now=lambda: iso(int(self.clock() * 1000)))
        self.fleets, self.linear = FakePoller(), FakePoller()
        acts = Acts(self.state, store, lambda: store, self.world)
        self.engine = Engine("ACME", self.state, self.fleets, self.linear, Now(acts), self.clock, lambda: DAY, 42)

    def turn(self) -> None:
        self.clock.t += 1
        stores = Stores(app=self.store, person=self.store)
        self.fleets.value = fleet.sessions(self.world)
        self.linear.value = linker.link("ACME", self.fleets.latest, self.clock, lambda: stores, lambda: DAY)
        self.fleets.at = self.clock()
        self.engine.tick()

    def hand(self, **fields: str) -> None:
        for found in self.world.rows:
            if found["name"] == NAME:
                found.update(fields)

    def captain(self, capsys: pytest.CaptureFixture[str]) -> list[str]:
        capsys.readouterr()
        Captain(self.world, clock=self.clock).context([])
        return capsys.readouterr().out.splitlines()


def test_one_story_runs_from_launch_through_an_answer_to_the_finished_stop(
    clone: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = building()
    store.assign("s1", ME)
    store.assign("s2", "someone-else")
    flow = Flow(store)
    flow.turn()
    flow.turn()
    assert flow.world.launches() == 1
    [launched] = flow.world.rows
    assert (launched["name"], flow.state.hands()[NAME].short) == (NAME, launched["id"])
    assert asks.HAND_START in [r.kind for r in store.records("s1")]

    asked(store, "s1")
    flow.hand(state="idle", status="idle")
    flow.turn()
    shown = flow.captain(capsys)
    assert shown[0] == "## ● ACME-1 needs you"
    [command] = [line[len("next: ") :] for line in shown if line.startswith("next: ")]
    assert command.startswith("decide context question ")

    answer(state, Decide(store), capsys, command.split()[2:], "SQS")
    flow.turn()
    [message] = flow.state.pending(NAME)
    assert message.acked is None and "SQS" in message.body
    assert "next:" not in "\n".join(flow.captain(capsys))
    assert flow.world.launches() == 1

    flow.hand(state="working", status="busy")
    Mail().apply(["ACME-1", "ack", str(message.seq)])
    assert flow.state.pending(NAME) == []

    store.set_issue_state("s1", "Done")
    flow.turn()
    assert ["claude", "stop", launched["id"]] not in flow.world.calls
    flow.hand(state="idle", status="idle")
    flow.turn()
    assert ["claude", "stop", launched["id"]] in flow.world.calls
    assert "status" not in flow.world.rows[0]
    assert flow.world.launches() == 1


def test_a_spike_asked_through_hand_is_launched_by_the_engine(clone: Path, tmp_path: Path):
    store = empty_feature()
    question = tmp_path / "question.md"
    question.write_text(QUESTION)
    flow = Flow(store)
    Hand(store, flow.world, person=store).apply(["spike", "--repository", "widgets", "--file", str(question), *TEAM])
    assert flow.world.launches() == 0
    flow.turn()
    flow.turn()
    name = f"spike-{spikes.key(P1, QUESTION)}"
    [launched] = flow.world.rows
    assert (launched["name"], launched["cwd"]) == (name, str(clone / ".worktrees" / name))
    assert ["git", "worktree", "add", "--detach", launched["cwd"], "origin/HEAD"] in flow.world.calls
    assert flow.world.launches() == 1
    assert "stalled" not in [item["kind"] for item in status.read(answers.state_dir())["items"]]


def test_an_idle_hand_is_nudged_then_resumed_then_brought_to_the_person(
    clone: Path, state: Path, capsys: pytest.CaptureFixture[str]
):
    store = building()
    store.assign("s1", ME)
    store.assign("s2", "someone-else")
    flow = Flow(store)
    flow.turn()
    flow.turn()
    [launched] = flow.world.rows
    flow.hand(state="blocked", status="busy")
    flow.state.turn_ended(NAME)
    for _ in range(121):
        flow.turn()
    [nudge] = flow.state.pending(NAME)
    assert (nudge.kind, flow.state.hands()[NAME].restarts) == (wakes.NUDGE, 1)
    shown = status.read(answers.state_dir())
    assert [h["idle"] for h in shown["hands"]] == [True]
    assert "stalled" not in [item["kind"] for item in shown["items"]]

    for _ in range(125):
        flow.turn()
    assert ["claude", "stop", launched["id"]] in flow.world.calls
    assert any("--resume" in call for call in flow.world.calls)
    assert flow.state.pending(NAME)[0].delivered is not None

    flow.hand(state="blocked", status="busy")
    flow.state.turn_ended(NAME)
    for _ in range(121):
        flow.turn()
    shown = status.read(answers.state_dir())
    assert [item["kind"] for item in shown["items"]] == ["stalled"]
    assert [m.kind for m in flow.state.pending(NAME)] == [wakes.NUDGE]

    [resumed] = [row for row in flow.world.rows if "status" in row]
    answer(state, Decide(store), capsys, ["stalled", "ACME-1"], "Restart")
    resumes = sum("--resume" in call for call in flow.world.calls)
    for _ in range(5):
        flow.turn()
    assert ["claude", "stop", resumed["id"]] in flow.world.calls
    assert sum("--resume" in call for call in flow.world.calls) == resumes + 1
    assert "stalled" not in [item["kind"] for item in status.read(answers.state_dir())["items"]]
