from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from deckhand import queue
from deckhand.reconcile import Action, Adopt, Post, Resume
from deckhand.step import Refusal
from deckhand.store.local import default_path
from deckhand.store.state import LocalState
from deckhand.worker import Runs, Worker, opened_acts
from tests.shell import Shell

NAME = "hand-ACME-1"


def drained(worker: Worker, count: int) -> list[tuple[Action, str | None]]:
    found: list[tuple[Action, str | None]] = []
    for _ in range(500):
        found += worker.results()
        if len(found) >= count:
            break
        threading.Event().wait(0.01)
    return found


def test_the_worker_runs_actions_on_its_own_connection_and_closes_it():
    with LocalState(default_path()) as rows:
        rows.want(NAME, "ACME-1", "s1", queue.STORY_HAND, "/w/acme-1")
    worker = Worker(lambda: opened_acts(Shell()))
    worker.start()
    adopt = Adopt(NAME, "id-ACME-1", "sid-ACME-1")
    worker.submit(adopt)
    assert drained(worker, 1) == [(adopt, None)]
    worker.stop()
    with LocalState(default_path()) as rows:
        assert rows.hands()[NAME].session == "sid-ACME-1"


def test_two_actions_for_one_name_both_run_in_order_and_hold_it_busy():
    gate, inside = threading.Event(), threading.Event()
    ran: list[Action] = []

    class Slow:
        def run(self, action: Action) -> str | None:
            ran.append(action)
            inside.set()
            gate.wait(5)
            return None

    @contextmanager
    def opened() -> Iterator[Runs]:
        yield Slow()

    worker = Worker(opened)
    worker.start()
    post = Post(NAME, "sync:s2:hand-ACME-1", "sync", "body", "s1", None)
    resume = Resume(NAME, "sid-ACME-1", "/w", "text", (1,))
    worker.submit(post)
    worker.submit(resume)
    assert inside.wait(5)
    assert worker.busy() == frozenset({NAME})
    gate.set()
    assert drained(worker, 2) == [(post, None), (resume, None)]
    worker.stop()
    assert ran == [post, resume] and worker.busy() == frozenset()


def test_a_state_that_cannot_open_answers_every_action_with_a_line(tmp_path: Path):
    @contextmanager
    def opened() -> Iterator[Runs]:
        raise Refusal("deckhand.db cannot be opened")
        yield

    worker = Worker(opened)
    worker.start()
    adopt = Adopt(NAME, "id-ACME-1", "sid-ACME-1")
    worker.submit(adopt)
    assert drained(worker, 1) == [(adopt, "ACME-1: deckhand.db cannot be opened")]
    worker.stop()
