from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

import pytest

from deckhand import launch
from deckhand.features import FEATURE_LABEL
from deckhand.linear.client import LinearError
from deckhand.ops import create_id
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.feature import Feature
from deckhand.store.fake import FakeStore
from tests.test_step_decide import Connected, roles_connected

TEAM = ["--team-key", "ACME"]
TODAY = date(2026, 11, 20)


def step(store: FakeStore) -> Feature:
    return Feature(store, lambda: TODAY)


P1 = create_id("ACME:Alpha", "feature")
P2 = create_id("ACME:Beta", "feature")


def parked(store: FakeStore, name: str = "Alpha") -> str:
    id = create_id(f"ACME:{name}", "feature")
    store.create_project(id, "ACME", name, "Budget: 9 weeks.", [FEATURE_LABEL])
    return id


def test_pick_refuses_while_another_feature_is_open():
    store = FakeStore()
    parked(store, "Alpha")
    parked(store, "Beta")
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    store.writes.clear()
    with pytest.raises(Refusal, match=rf"Alpha \({P1}\)"):
        step(store).apply(["pick", *TEAM, "--project", P2, "--weeks", "1"])
    assert store.writes == []


def test_pick_sets_dates_from_budget():
    store = FakeStore()
    parked(store)
    step(store).apply(["pick", *TEAM, "--project", P1, "--weeks", "2"])
    assert store.writes[-1] == ("set_project_dates", P1, "2026-11-20", "2026-12-04")


@pytest.mark.parametrize("weeks", ["0", "3", "x", ""])
def test_weeks_other_than_1_or_2_refuses(weeks: str):
    store = FakeStore()
    parked(store)
    store.writes.clear()
    with pytest.raises(Refusal, match="1 or 2"):
        step(store).apply(["pick", *TEAM, "--project", P1, "--weeks", weeks])
    assert store.writes == []


def test_reference_marked_as_reference_in_description():
    store = FakeStore()
    args = ["new", *TEAM, "--name", "Alpha", "--reference", "ACME-4"]
    step(store).apply(args)
    step(store).apply(args)
    [made] = store.team_projects("ACME")
    assert made["id"] == create_id("ACME:Alpha", "feature")
    assert "Reference (not a decision):\n- ACME-4" in made["description"]
    assert made["start_date"] is None
    assert made["labels"] == ["deckhand"]
    assert store.writes == [("create_project", made["id"])]


def test_new_labels_a_project_an_older_run_left_unlabeled():
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "")
    step(store).apply(["new", *TEAM, "--name", "Alpha"])
    assert store.project(P1)["labels"] == ["deckhand"]
    assert [w[0] for w in store.writes] == ["create_project", "set_project_labels"]


def test_park_refuses_with_an_approved_story():
    store = FakeStore()
    parked(store)
    step(store).apply(["pick", *TEAM, "--project", P1, "--weeks", "2"])
    store.create_issue("i1", "ACME", "t", "", P1, [])
    store.put_record("i1", Record("feature-approval", "i1", "1", "me", {}))
    store.writes.clear()
    with pytest.raises(Refusal, match="feature-approval"):
        step(store).apply(["park", *TEAM, "--project", P1])
    assert store.writes == []


def test_park_clears_dates():
    store = FakeStore()
    parked(store)
    step(store).apply(["pick", *TEAM, "--project", P1, "--weeks", "2"])
    step(store).apply(["park", *TEAM, "--project", P1])
    assert store.writes[-1] == ("set_project_dates", P1, None, None)


def test_context_json_has_phase_budget_and_parked(capsys: pytest.CaptureFixture[str]):
    store = FakeStore()
    parked(store, "Alpha")
    parked(store, "Beta")
    step(store).apply(["pick", *TEAM, "--project", P1, "--weeks", "1"])
    capsys.readouterr()
    step(store).context([*TEAM, "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert facts["open"][0]["phase"] == "planning"
    assert facts["open"][0]["budget"] == {"days_used": 0, "budget_days": 7, "planning_note": False, "over": False}
    assert facts["parked"] == [{"id": P2, "name": "Beta"}]


def test_context_reads_budget_from_dates_not_description(capsys: pytest.CaptureFixture[str]):
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "Budget: 9 weeks.", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-30")
    step(store).context([*TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["open"][0]["budget"]["budget_days"] == 14


def test_context_with_two_open_features_reports_both(capsys: pytest.CaptureFixture[str]):
    store = FakeStore()
    for id, name in ((P1, "Alpha"), (P2, "Beta")):
        store.create_project(id, "ACME", name, "", [FEATURE_LABEL])
        store.set_project_dates(id, "2026-11-16", "2026-11-23")
    step(store).context([*TEAM, "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert [f["id"] for f in facts["open"]] == sorted([P1, P2])
    assert "more than one feature is open" in facts["anomaly"]
    step(store).context(TEAM)
    assert "more than one feature is open" in capsys.readouterr().out


def test_context_reports_and_pick_refuses_odd_budget_dates(capsys: pytest.CaptureFixture[str]):
    store = FakeStore()
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-26")
    step(store).context([*TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["open"][0]["budget"] is None
    store.create_project(P2, "ACME", "Beta", "", [FEATURE_LABEL])
    store.writes.clear()
    with pytest.raises(Refusal, match="Alpha"):
        step(store).apply(["pick", *TEAM, "--project", P2, "--weeks", "1"])
    assert store.writes == []


def test_a_hand_made_project_with_dates_is_not_a_feature(capsys: pytest.CaptureFixture[str]):
    store = FakeStore()
    store.create_project("hand", "ACME", "Hand made", "")
    store.set_project_dates("hand", "2026-11-16", "2026-11-23")
    step(store).apply(["new", *TEAM, "--name", "Alpha"])
    alpha = create_id("ACME:Alpha", "feature")
    capsys.readouterr()
    step(store).context([*TEAM, "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert (facts["open"], facts["parked"]) == ([], [{"id": alpha, "name": "Alpha"}])
    step(store).apply(["pick", *TEAM, "--project", alpha, "--weeks", "1"])
    assert store.writes[-1] == ("set_project_dates", alpha, "2026-11-20", "2026-11-27")


@pytest.mark.parametrize("action", [["pick", "--weeks", "1"], ["park"]])
def test_pick_and_park_refuse_another_teams_feature_and_a_hand_made_project(action: list[str]):
    store = FakeStore()
    step(store).apply(["new", "--team-key", "OTHER", "--name", "Alpha"])
    store.create_project("hand", "ACME", "Hand made", "")
    store.writes.clear()
    for project in (create_id("OTHER:Alpha", "feature"), "hand"):
        with pytest.raises(Refusal, match="not a feature of team ACME"):
            step(store).apply([action[0], *TEAM, "--project", project, *action[1:]])
    assert store.writes == []


def test_feature_writes_as_person(monkeypatch: pytest.MonkeyPatch):
    roles = roles_connected(monkeypatch)
    with pytest.raises(Connected):
        Feature().apply(["new", *TEAM, "--name", "Alpha"])
    assert roles == ["person"]


def test_context_names_the_input_folder_it_makes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    step(FakeStore()).context(TEAM)
    folder = tmp_path / launch.INPUT
    assert f"input folder: {folder}" in capsys.readouterr().out.splitlines()
    assert folder.is_dir()
    step(FakeStore()).context([*TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["input_folder"] == str(folder)


class Unreachable:
    def __getattr__(self, name: str):
        raise LinearError("network", "could not reach Linear")


def test_context_names_the_input_folder_before_any_linear_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(LinearError):
        Feature(Unreachable()).context(TEAM)
    assert capsys.readouterr().out == f"input folder: {tmp_path / launch.INPUT}\n"
