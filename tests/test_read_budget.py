from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from deckhand import claims
from deckhand.features import FEATURE_LABEL
from deckhand.ops import create_id
from deckhand.steps import build, captain, hand
from deckhand.steps.build import Build
from deckhand.steps.captain import Captain
from deckhand.steps.decide import Decide
from deckhand.steps.hand import Hand
from deckhand.steps.review import Review
from deckhand.store.cached import READS, CachedStore
from deckhand.store.fake import FakeStore
from tests.conftest import CountingStore
from tests.test_implplan import BASE, Git, repo
from tests.test_step_build import Shell as BuildShell
from tests.test_step_hand import TEAM, Shell, clone
from tests.test_step_review import planned

__all__ = ["clone", "repo"]

BUDGET = 12


def released(store: FakeStore, name: str, start: str) -> None:
    id = create_id(f"ACME:{name}", "feature")
    store.create_project(id, "ACME", name, "", [FEATURE_LABEL])
    store.set_project_dates(id, start, None)
    for n in range(5):
        store.create_issue(f"{name.lower()}{n}", "ACME", f"{name} {n}", "", id, ["widgets"])
        store.set_issue_state(f"{name.lower()}{n}", "Done")


@pytest.fixture
def team(tmp_path: Path, repo: Path) -> FakeStore:
    store = planned(tmp_path, repo)
    for name, start in (("Beta", "2026-10-19"), ("Gamma", "2026-10-26"), ("Delta", "2026-11-02")):
        released(store, name, start)
    return store


@pytest.fixture
def opened(
    team: FakeStore, monkeypatch: pytest.MonkeyPatch, counting_store: Callable[[FakeStore], CountingStore]
) -> dict[str, CountingStore]:
    found = {"app": counting_store(team), "person": counting_store(team)}
    for module in (captain, build, hand, claims):
        monkeypatch.setattr(module, "open_store", lambda role: CachedStore(found[role]))
    return found


def reads(counting: CountingStore) -> list[tuple]:
    return [call for call in counting.log if call[0] in READS]


def repeated(counting: CountingStore) -> list[tuple]:
    return [call for call, n in Counter(reads(counting)).items() if n > 1]


def run_captain(capsys: pytest.CaptureFixture[str]) -> None:
    Captain(None, Shell(), today=lambda: date(2026, 11, 17), clock=lambda: 1795000000.0).context(["--json", *TEAM])
    facts = json.loads(capsys.readouterr().out)
    assert (facts["feature"], [row["ref"] for row in facts["order"]]) == ("Alpha", ["ACME-1", "ACME-2"])


def test_captain_context_reads_each_linear_object_at_most_once(
    clone: Path, opened: dict[str, CountingStore], capsys: pytest.CaptureFixture[str]
):
    run_captain(capsys)
    assert repeated(opened["app"]) == []
    assert reads(opened["person"]) == [("viewer", (), ())]


def test_captain_context_stays_within_its_read_budget(
    clone: Path, opened: dict[str, CountingStore], capsys: pytest.CaptureFixture[str]
):
    run_captain(capsys)
    assert len(reads(opened["app"])) <= BUDGET


def test_build_review_and_decide_contexts_read_each_object_at_most_once(
    repo: Path, opened: dict[str, CountingStore], capsys: pytest.CaptureFixture[str]
):
    runs = {
        "build": lambda: Build(None, BuildShell(), repo).context(["ACME-1", *TEAM, "--json"]),
        "review": lambda: Review(None, Git(BASE), repo).context(["ACME-1", "--stage", "plan", *TEAM, "--json"]),
        "decide": lambda: Decide(None).context(["tier", "ACME-1", *TEAM, "--json"]),
    }
    found = {}
    for name, run in runs.items():
        for counting in opened.values():
            counting.log.clear()
        run()
        capsys.readouterr()
        found[name] = {role: repeated(counting) for role, counting in opened.items()}
    assert found == {name: {"app": [], "person": []} for name in runs}


def test_hand_context_reads_each_object_at_most_once(
    clone: Path, opened: dict[str, CountingStore], capsys: pytest.CaptureFixture[str]
):
    Hand(None, Shell()).context([*TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["feature"] == "Alpha"
    assert repeated(opened["app"]) == []
    assert reads(opened["person"]) == [("viewer", (), ())]
