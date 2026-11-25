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
from deckhand.steps.review import FeatureReview
from deckhand.steps.stories import Stories
from deckhand.store.fake import FakeStore
from tests.test_skills import named_commands, split

TEAM: list[str] = []
TODAY = date(2026, 11, 20)
ITEM = "Keep orders in one table"
DECISION = {"item": ITEM, "draft": "Yes"}


class Session:
    def __init__(self, store: FakeStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        self.store, self.tmp, self.monkeypatch, self.capsys = store, tmp_path, monkeypatch, capsys
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "config.toml").write_text('team_key = "ACME"\n')
        monkeypatch.setenv("DECKHAND_CONFIG_DIR", str(tmp_path / "config"))
        cli.main(["hook", "context"])
        for name, step in (
            ("feature", Feature(store, lambda: TODAY)),
            ("plan", Plan(store)),
            ("stories", Stories(store)),
            ("decide", Decide(store)),
            ("feature-review", FeatureReview(store)),
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

    def review_feature(self, run) -> None:
        shown = run("feature-review", "context", *TEAM)
        target = next(line for line in shown.splitlines() if line.startswith("target_hash: "))
        findings = self.write("findings.json", "[]")
        run("feature-review", "apply", "round", *TEAM, "--findings", findings, "--target", target.split()[1])

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
            "tier": "concurrency",
            "state_chart": "queued -> sent",
            "after_merge": ["drain the old queue"],
            "decisions": [DECISION],
        },
        {
            "key": "page",
            "title": "Order page",
            "outcome": "An order is shown.",
            "repository": "web",
            "depends_on": ["queue"],
            "unknowns": [],
            "new_here": [],
            "tier": "none",
        },
        {
            "key": "owner",
            "title": "Order owner",
            "outcome": "Only the owner reads an order.",
            "repository": "api",
            "depends_on": [],
            "unknowns": [],
            "new_here": [],
            "tier": "data_rule",
        },
    ]
    session.run("stories", "apply", *TEAM, "--file", session.write("map.json", json.dumps(stories)))
    queue, page, owner = (s["id"] for s in sorted(store.project_issues(alpha), key=lambda s: s["number"]))
    assert store.relations(page) == [(queue, page)]
    assert store.relations(owner) == []
    assert session.facts()["phase"] == "planning"

    assert session.facts()["gaps"] == [
        "the Contract is not approved",
        "the Invariants are not approved",
        f"ACME-1: decision item {ITEM} is not decided",
        "the feature review has not run",
    ]
    session.decide("contract", alpha, "Approve")
    session.decide("invariants", alpha, "Approve")
    session.decide("decision-item", ITEM, "Yes", "--story", "ACME-1")

    assert session.facts()["gaps"] == ["the feature review has not run"]
    session.review_feature(session.run)
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


SKILL = Path(__file__).resolve().parent.parent / "skills" / "feature" / "SKILL.md"


def shape(tokens: list[str]) -> tuple[str, str, str]:
    name, verb, *rest = tokens
    return name, verb, rest[0] if (name, verb) == ("feature", "apply") and rest else ""


class SkillSession(Session):
    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.order: list[tuple[str, str, str]] = []
        self.flags: dict[tuple[str, str, str], set[str]] = {}
        for tokens in named_commands(split(SKILL)[1]):
            key = shape(tokens)
            if key not in self.order:
                self.order.append(key)
            self.flags.setdefault(key, set()).update(t for t in tokens if t.startswith("--"))
        self.used: list[tuple[str, str, str]] = []

    def skill(self, *argv: str) -> str:
        key = shape(list(argv))
        assert key in self.order, f"the skill does not name deckhand {' '.join(key)}"
        unnamed = {t for t in argv if t.startswith("--")} - self.flags[key]
        assert not unnamed, f"the skill does not name {unnamed} for deckhand {' '.join(key)}"
        if key not in self.used:
            self.used.append(key)
        return self.run(*argv)

    def ask(self, kind: str, subject: str, answer: str, *extra: str) -> None:
        first, _, rest = self.skill("decide", "context", kind, subject, *extra, *TEAM).partition("\n")
        nonce, tool_input = first.removeprefix("nonce: "), json.loads(rest)
        assert self.run("hook", "apply", "ask", stdin=json.dumps({"tool_input": tool_input})) == ""
        asked = tool_input["questions"][0]
        hook = {
            "session_id": "sess",
            "tool_use_id": "tool",
            "tool_response": {"questions": [asked], "answers": {asked["question"]: answer}},
        }
        self.run("hook", "apply", "answer", stdin=json.dumps(hook))
        assert self.skill("decide", "apply", nonce, *TEAM).startswith("done: recorded")

    def gaps(self) -> list[str]:
        shown = self.skill("feature", "context", *TEAM)
        return [line.removeprefix("gap: ") for line in shown.splitlines() if line.startswith("gap: ")]


def test_the_feature_skill_alone_reaches_building(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    store = FakeStore()
    store.add_repository_label("ACME", "api")
    session = SkillSession(store, tmp_path, monkeypatch, capsys)

    session.skill("feature", "apply", "new", *TEAM, "--name", "Alpha")
    [alpha] = (p["id"] for p in store.team_projects("ACME"))
    session.skill("feature", "apply", "pick", *TEAM, "--project", alpha, "--weeks", "1")
    for section in planning.SECTIONS:
        assert section in session.skill("plan", "context", *TEAM)
        path = session.write(f"{section}.md", f"The {section} of Alpha.")
        session.skill("plan", "apply", section, *TEAM, "--file", path)

    assert "decisions: " in session.skill("stories", "context", *TEAM)
    stories = [
        {"key": "queue", "title": "Queue orders", "outcome": "Queued once.", "repository": "api", "depends_on": []},
        {"key": "owner", "title": "Order owner", "outcome": "Owner only.", "repository": "api", "depends_on": []},
    ]
    for entry in stories:
        entry.update(unknowns=[], new_here=[], tier="data_rule")
    stories[0].update(decisions=[DECISION], tier="multi_step", state_chart="placed -> queued")
    saved = session.skill("stories", "apply", *TEAM, "--file", session.write("map.json", json.dumps(stories)))
    assert [line for line in saved.splitlines() if line.startswith("story: ")] == [
        "story: ACME-1 queue",
        "story: ACME-2 owner",
    ]

    session.ask("contract", alpha, "Approve")
    session.ask("invariants", alpha, "Approve")

    [gap] = [found for found in session.gaps() if ": decision item " in found]
    ref, _, rest = gap.partition(": decision item ")
    session.ask("decision-item", rest.removesuffix(" is not decided"), "Yes", "--story", ref)
    assert session.gaps() == ["the feature review has not run"]
    session.review_feature(session.skill)
    assert session.gaps() == []
    session.ask("feature-approval", alpha, "Approve")

    assert features.phase(features.open_feature(store, "ACME")) is Phase.BUILDING
    assert session.used == [key for key in session.order if key in session.used]
