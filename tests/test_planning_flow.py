from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import pytest

from deckhand import cli, features, planning
from deckhand.features import Phase
from deckhand.step import REGISTRY
from deckhand.steps.decide import Decide
from deckhand.steps.feature import Feature
from deckhand.steps.plan import Plan
from deckhand.steps.sitting import Sitting
from deckhand.steps.stories import Stories
from deckhand.store.fake import FakeStore
from deckhand.tiers import FACTS, NONE

TEAM = ["--team-key", "ACME"]
TODAY = date(2026, 11, 20)
ITEM = "Keep orders in one table"
DATA_RULE = next(f.question for f in FACTS if f.key == "data_rule")
CONCURRENCY = next(f.question for f in FACTS if f.key == "concurrency")


class Session:
    def __init__(self, store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        self.store, self.tmp, self.monkeypatch, self.capsys = store, tmp_path, monkeypatch, capsys
        cli.main(["hook", "context"])
        for name, step in (
            ("feature", Feature(store, lambda: TODAY)),
            ("plan", Plan(store)),
            ("stories", Stories(store)),
            ("decide", Decide(store)),
            ("sitting", Sitting(store)),
        ):
            monkeypatch.setitem(REGISTRY, name, step)
        capsys.readouterr()

    def run(self, *argv: str, stdin: str = "") -> str:
        self.monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        code = cli.main(list(argv))
        captured = self.capsys.readouterr()
        assert code == 0, captured.err
        return captured.out

    def refuse(self, *argv: str) -> str:
        assert cli.main(list(argv)) == 1
        return self.capsys.readouterr().err

    def write(self, name: str, text: str) -> str:
        path = self.tmp / name
        path.write_text(text)
        return str(path)

    def save(self, section: str, text: str) -> None:
        self.run("plan", "apply", section, *TEAM, "--file", self.write(f"{section}.md", text))

    def decide(self, kind: str, subject: str, answer: str, *extra: str) -> None:
        shown = json.loads(self.run("decide", "context", kind, subject, *extra, *TEAM, "--json"))
        asked = shown["tool_input"]["questions"][0]
        hook = {
            "session_id": "sess",
            "tool_use_id": "tool",
            "tool_response": {
                "questions": [asked],
                "answers": {asked["question"]: answer},
            },
        }
        self.run("hook", "apply", "answer", stdin=json.dumps(hook))
        out = self.run("decide", "apply", shown["nonce"], *TEAM)
        assert out.startswith("done: recorded")

    def facts(self) -> dict:
        [held] = json.loads(self.run("feature", "context", *TEAM, "--json"))["open"]
        return held


def test_a_feature_is_planned_approved_and_voided_by_a_contract_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    store = FakeStore()
    store.add_repository_label("ACME", "api")
    store.add_repository_label("ACME", "web")
    session = Session(store, tmp_path, monkeypatch, capsys)

    session.run("feature", "apply", "new", *TEAM, "--name", "Alpha")
    session.run("feature", "apply", "new", *TEAM, "--name", "Beta")
    alpha, beta = (p["id"] for p in sorted(store.team_projects("ACME"), key=lambda p: p["name"]))
    session.run("feature", "apply", "pick", *TEAM, "--project", alpha, "--weeks", "1")

    for section in planning.SECTIONS:
        session.save(section, f"The {section} of Alpha.")
    assert "next: story map" in session.run("plan", "context", *TEAM)

    stories = [
        {
            "key": "queue",
            "title": "Queue orders",
            "outcome": "An order is queued once.",
            "repository": "api",
            "depends_on": [],
            "unknowns": [],
            "new_here": [],
            "decisions": [ITEM],
        },
        {
            "key": "page",
            "title": "Order page",
            "outcome": "An order is shown.",
            "repository": "web",
            "depends_on": ["queue"],
            "unknowns": [],
            "new_here": [],
        },
        {
            "key": "owner",
            "title": "Order owner",
            "outcome": "Only the owner reads an order.",
            "repository": "api",
            "depends_on": [],
            "unknowns": [],
            "new_here": [],
        },
    ]
    session.run("stories", "apply", *TEAM, "--file", session.write("map.json", json.dumps(stories)))
    queue, page, owner = (s["id"] for s in sorted(store.project_issues(alpha), key=lambda s: s["number"]))
    assert store.relations(page) == [(queue, page)]
    assert store.relations(owner) == []
    assert session.facts()["phase"] == "planning"

    session.decide("contract", alpha, "Approve")
    session.decide("invariants", alpha, "Approve")
    for ref, answer in (("ACME-1", CONCURRENCY), ("ACME-2", NONE), ("ACME-3", DATA_RULE)):
        session.decide("tier", ref, answer)
    session.decide("state-chart", "ACME-1", "Approve")
    for ref in ("ACME-1", "ACME-2", "ACME-3"):
        shown = session.run("sitting", "context", ref, *TEAM)
        assert shown.startswith(f"story: {ref}")
        session.run("sitting", "apply", ref, *TEAM, "--file", session.write(f"{ref}.md", f"Answers for {ref}."))
        session.decide("sitting", ref, "Done")
    session.decide("decision-item", ITEM, "Keep", "--story", "ACME-1")

    assert session.facts()["gaps"] == []
    session.decide("feature-approval", alpha, "Approve")
    held = session.facts()
    assert (held["phase"], held["gaps"], held["approval_void"]) == ("building", [], False)

    session.save("Contract", "POST /orders, changed.")
    held = session.facts()
    assert held["phase"] == "planning"
    assert held["approval_void"] is True
    assert "the Contract changed after it was approved" in held["gaps"]
    assert features.phase(features.open_feature(store, "ACME")) is Phase.PLANNING

    store.writes.clear()
    refusal = session.refuse("feature", "apply", "pick", *TEAM, "--project", beta, "--weeks", "1")
    assert f"Alpha ({alpha}) is still open" in refusal
    assert store.writes == []
