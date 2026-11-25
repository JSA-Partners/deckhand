from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from deckhand import answers, decisions, features, ops, planning, spikes, tiers, versions
from deckhand.features import FEATURE_LABEL
from deckhand.ops import create_id
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.feature import Feature
from deckhand.steps.plan import Plan
from deckhand.steps.stories import Stories
from deckhand.store.fake import FakeStore

P1 = create_id("ACME:Alpha", "feature")

TEAM = ["--team-key", "ACME"]


def story_id(key: str) -> str:
    return create_id(P1, "story:" + key)


def entry(key: str, **over: object) -> dict:
    found: dict = {
        "key": key,
        "title": f"Story {key}",
        "outcome": f"Outcome of {key}.",
        "repository": "widgets",
        "depends_on": [],
        "unknowns": [],
        "new_here": [],
        "tier": "none",
    }
    return {**found, **over}


def setup() -> FakeStore:
    store = FakeStore()
    store.add_repository_label("ACME", "widgets")
    store.create_project(P1, "ACME", "Alpha", "", [FEATURE_LABEL])
    store.set_project_dates(P1, "2026-11-16", "2026-11-23")
    return store


def run(store: FakeStore, tmp_path: Path, entries: object) -> None:
    path = tmp_path / "map.json"
    path.write_text(json.dumps(entries))
    Stories(store).apply([*TEAM, "--file", str(path)])


def test_map_creates_stubs_with_labels_and_blockers(tmp_path: Path):
    store = setup()
    run(
        store,
        tmp_path,
        [
            entry("api", unknowns=["rate limits"], new_here=["queue"]),
            entry("ui", depends_on=["api"]),
        ],
    )
    api, ui = store.issue(story_id("api")), store.issue(story_id("ui"))
    assert api is not None and ui is not None
    assert api["project"] == P1
    assert api["team"] == "ACME"
    assert api["title"] == "Story api"
    assert api["labels"] == ["widgets"]
    assert "Outcome of api." in api["description"]
    assert "- rate limits" in api["description"]
    assert "- queue" in api["description"]
    assert store.relations(story_id("api")) == [(story_id("api"), story_id("ui"))]
    [stub] = [r for r in store.records(story_id("api")) if r.kind == "stub"]
    assert stub.subject == "api"
    assert stub.data == {
        "unknowns": ["rate limits"],
        "new_here": ["queue"],
        "decisions": [],
        "after_merge": [],
        "description": api["description"],
    }


def test_map_keeps_expected_decisions_in_the_stub_and_reruns_write_nothing(tmp_path: Path):
    store = setup()
    entries = [entry("api", decisions=[{"item": "Keep orders in one table", "draft": "Yes"}])]
    run(store, tmp_path, entries)
    [stub] = [r for r in store.records(story_id("api")) if r.kind == "stub"]
    assert stub.data["decisions"] == [{"item": "Keep orders in one table", "draft": "Yes"}]
    store.writes.clear()
    run(store, tmp_path, entries)
    assert store.writes == []


@pytest.mark.parametrize(
    "bad",
    [
        "Keep it",
        ["Keep it"],
        [{"item": "Keep it"}],
        [{"item": "Keep it", "draft": " "}],
        [{"item": "Keep it", "draft": "Yes", "why": "x"}],
        [{"item": "Keep it", "draft": 3}],
    ],
)
def test_map_refuses_a_malformed_decisions_field(tmp_path: Path, bad: object):
    store = setup()
    store.writes.clear()
    with pytest.raises(Refusal, match=r"entry 1 .*decisions? .*\{\"item\": text, \"draft\": text\}"):
        run(store, tmp_path, [entry("api"), entry("ui", decisions=bad)])
    assert store.writes == []


def test_map_refuses_a_decision_item_named_twice(tmp_path: Path):
    store = setup()
    store.writes.clear()
    twice = [{"item": "Greeting wording", "draft": "Bonjour"}, {"item": "Greeting wording", "draft": "Hola"}]
    with pytest.raises(Refusal, match="entry 1 decision 1 repeats the item Greeting wording"):
        run(store, tmp_path, [entry("api"), entry("ui", decisions=twice)])
    assert store.writes == []


def test_map_twice_writes_once(tmp_path: Path):
    store = setup()
    entries = [entry("api"), entry("ui", depends_on=["api"])]
    run(store, tmp_path, entries)
    store.writes.clear()
    run(store, tmp_path, entries)
    assert store.writes == []


def test_map_rerun_keeps_a_label_a_person_added_and_swaps_only_the_repository(tmp_path: Path):
    store = setup()
    store.add_repository_label("ACME", "gadgets")
    run(store, tmp_path, [entry("api")])
    store.set_labels(story_id("api"), ["widgets", "urgent"])
    store.writes.clear()
    run(store, tmp_path, [entry("api")])
    assert store.writes == []
    run(store, tmp_path, [entry("api", repository="gadgets")])
    found = store.issue(story_id("api"))
    assert found is not None
    assert sorted(found["labels"]) == ["gadgets", "urgent"]


def test_map_changed_outcome_updates_the_description(tmp_path: Path):
    store = setup()
    run(store, tmp_path, [entry("api")])
    store.writes.clear()
    run(store, tmp_path, [entry("api", outcome="A better outcome.")])
    found = store.issue(story_id("api"))
    assert found is not None
    assert "A better outcome." in found["description"]
    assert [w[:3] for w in store.writes] == [
        ("set_issue_text", story_id("api"), "Story api"),
        ("put_record", story_id("api"), "stub"),
    ]


def test_map_refuses_cycle(tmp_path: Path):
    store = setup()
    store.writes.clear()
    with pytest.raises(Refusal, match="cycle"):
        run(store, tmp_path, [entry("a", depends_on=["b"]), entry("b", depends_on=["a"])])
    assert store.writes == []


def test_map_refuses_unknown_repository(tmp_path: Path):
    store = setup()
    store.writes.clear()
    with pytest.raises(Refusal, match="gadgets is not a repository label of team ACME; its labels: widgets"):
        run(store, tmp_path, [entry("a"), entry("b", repository="gadgets")])
    assert store.writes == []


def test_map_refuses_a_repeated_key_and_an_unknown_dependency(tmp_path: Path):
    store = setup()
    with pytest.raises(Refusal, match="more than once"):
        run(store, tmp_path, [entry("a"), entry("a")])
    with pytest.raises(Refusal, match="nowhere"):
        run(store, tmp_path, [entry("a", depends_on=["nowhere"])])
    assert [w for w in store.writes if w[0] != "create_project" and w[0] != "set_project_dates"] == []


@pytest.mark.parametrize(
    ("entries", "match"),
    [
        ({"key": "a"}, "list"),
        (["a"], "entry 0"),
        ([entry("a", title=3)], r"entry 0.*title"),
        ([entry("a"), entry("b", depends_on="a")], r"entry 1.*depends_on"),
        ([entry("a"), {"key": "b"}], r"entry 1.*title"),
        ([entry("a", extra=1)], r"entry 0.*extra"),
        ([{**entry("a"), "key": ""}], r"entry 0.*key"),
    ],
)
def test_map_shape_is_checked(tmp_path: Path, entries: object, match: str):
    store = setup()
    store.writes.clear()
    with pytest.raises(Refusal, match=match):
        run(store, tmp_path, entries)
    assert store.writes == []


def test_map_without_an_open_feature_is_refused(tmp_path: Path):
    store = FakeStore()
    store.add_repository_label("ACME", "widgets")
    with pytest.raises(Refusal, match="no feature is open"):
        run(store, tmp_path, [entry("a")])


def test_map_over_budget_hint_notes_but_does_not_refuse(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    store = setup()
    run(store, tmp_path, [entry(f"s{n}") for n in range(9)])
    assert len(store.project_issues(P1)) == 9
    capsys.readouterr()
    Feature(store, lambda: date(2026, 11, 17)).context([*TEAM, "--json"])
    held = json.loads(capsys.readouterr().out)["open"][0]
    assert held["story_hint"] == {"stories": 9, "hint": 8, "over": True}


def approve(store: FakeStore, *keys: str) -> None:
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    version = versions.version_for(store, feature, features.APPROVAL, P1)
    for key in keys:
        store.put_record(story_id(key), Record(features.APPROVAL, P1, version, "person:s", {}))


def test_story_added_after_approval_needs_missed_by_and_a_cut(tmp_path: Path):
    store = setup()
    run(store, tmp_path, [entry("a"), entry("c")])
    approve(store, "a", "c")
    store.writes.clear()
    kept = entry("a")
    for bad, match in [
        (entry("b"), "missed_by"),
        (entry("b", missed_by="whim", cuts="c"), "missed_by"),
        (entry("b", missed_by="rule"), "cuts"),
        (entry("b", missed_by="rule", cuts="ghost"), "ghost"),
        (entry("b", missed_by="rule", cuts="a"), "still in the map"),
    ]:
        with pytest.raises(Refusal, match=match):
            run(store, tmp_path, [kept, bad])
    assert store.writes == []
    run(store, tmp_path, [kept, entry("b", missed_by="rule", cuts="c")])
    [record] = [r for r in store.records(story_id("b")) if r.kind == "discovered"]
    assert record.data == {"missed_by": "rule", "cuts": "c"}
    run(store, tmp_path, [kept, entry("b", missed_by="spike", spends_budget=True)])
    assert any(r.data.get("spends_budget") for r in store.records(story_id("b")))


def test_a_voided_approval_keeps_the_rule_for_new_stories(tmp_path: Path):
    store = setup()
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    planning.save_section(store, feature, "Contract", "POST /orders.", False)
    run(store, tmp_path, [entry("a")])
    approve(store, "a")
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    planning.save_section(store, feature, "Contract", "POST /orders, changed.", False)
    feature = features.open_feature(store, "ACME")
    assert feature is not None and feature.reapproval_due
    assert features.phase(feature) is features.Phase.BUILDING
    store.writes.clear()
    with pytest.raises(Refusal, match="b is new after approval; it needs missed_by"):
        run(store, tmp_path, [entry("a"), entry("b")])
    assert store.writes == []


def test_plan_saves_and_prints_a_section(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    store = setup()
    path = tmp_path / "frame.md"
    path.write_text("Why we plan.\n")
    plan = Plan(store)
    plan.apply(["Frame", *TEAM, "--file", str(path), "--drafted"])
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    saved = planning.section(store, feature, "Frame")
    assert saved is not None
    assert saved.startswith("Drafted by an agent, accepted by the person.")
    capsys.readouterr()
    plan.context(["Frame", *TEAM])
    assert "Why we plan." in capsys.readouterr().out
    plan.context(["Shape", *TEAM])
    assert "empty" in capsys.readouterr().out
    plan.context([*TEAM, "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert facts["next"] == "Shape"
    assert facts["sections"]["Frame"] is True


def test_feature_context_names_the_next_planning_step(capsys: pytest.CaptureFixture[str]):
    store = setup()
    Feature(store).context([*TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["open"][0]["next_planning_step"] == "Frame"


FIELDS = {
    "key",
    "title",
    "outcome",
    "repository",
    "depends_on",
    "unknowns",
    "new_here",
    "tier",
    "state_chart",
    "after_merge",
    "decisions",
    "missed_by",
    "cuts",
    "spends_budget",
}


def test_context_prints_every_field_the_map_accepts(capsys: pytest.CaptureFixture[str]):
    store = setup()
    Stories(store).context([*TEAM, "--json"])
    assert set(json.loads(capsys.readouterr().out)["map"]) == FIELDS
    Stories(store).context(TEAM)
    shown = capsys.readouterr().out
    for field in FIELDS:
        assert re.search(rf"^  {field}: ", shown, re.MULTILINE), field
    assert "rule, example, invariant, spike, external" in shown
    assert "repositories: widgets" in shown.splitlines()
    Stories(store).context([*TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["repositories"] == ["widgets"]


def asked_spike(question: str = "Does the queue keep order?") -> str:
    key = spikes.key(P1, question)
    path = spikes.question_path(answers.state_dir(), key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(question)
    return key


def fleet_of(*rows: dict) -> Callable:
    def runner(argv, cwd=None, stdin=None):
        return subprocess.CompletedProcess(argv, 0, json.dumps(list(rows)), "")

    return runner


def spike_row(key: str, state: str, status: str | None) -> dict:
    found = {"id": "i", "name": f"spike-{key}", "sessionId": "s", "cwd": "/w", "startedAt": 1795000000000}
    return found | {"state": state} | ({"status": status} if status else {})


def test_stories_refuse_while_spike_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    store, key = setup(), asked_spike()
    written = list(store.writes)
    path = tmp_path / "map.json"
    path.write_text(json.dumps([entry("a")]))
    runner = fleet_of(spike_row(key, "working", "busy"))
    with pytest.raises(Refusal, match=f"spike {key} is still running"):
        Stories(store, runner).apply([*TEAM, "--file", str(path)])
    assert store.writes == written
    Stories(store, fleet_of(spike_row(key, "done", None))).apply([*TEAM, "--file", str(path)])
    assert store.writes != written


@pytest.mark.parametrize("readable", [True, False])
def test_an_answered_spike_never_blocks_the_map_while_its_session_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, readable: bool
):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    store, question = setup(), "Does the queue keep order?"
    key = asked_spike(question)
    ops.ensure_document(store, P1, "spike:" + key, P1, spikes.TITLE + question, "Yes, in order.")
    path = tmp_path / "map.json"
    path.write_text(json.dumps([entry("a")]))

    def broken(argv, cwd=None, stdin=None):
        return subprocess.CompletedProcess(argv, 1, "", "no")

    runner = fleet_of(spike_row(key, "working", "busy")) if readable else broken
    Stories(store, runner).apply([*TEAM, "--file", str(path)])
    assert store.issue(story_id("a")) is not None


def test_stories_refuse_when_the_fleet_cannot_be_read_with_spikes_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    store = setup()
    asked_spike()
    path = tmp_path / "map.json"
    path.write_text(json.dumps([entry("a")]))

    def broken(argv, cwd=None, stdin=None):
        return subprocess.CompletedProcess(argv, 1, "", "no")

    with pytest.raises(Refusal, match="the fleet read failed"):
        Stories(store, broken).apply([*TEAM, "--file", str(path)])


def test_context_prints_when_the_labels_cannot_be_read(capsys: pytest.CaptureFixture[str]):
    store = setup()

    def fails(team: str) -> list[str]:
        raise Refusal("more than 250 labels")

    store.repository_labels = fails
    Stories(store).context(TEAM)
    assert "repositories: unknown, more than 250 labels" in capsys.readouterr().out.splitlines()


def test_the_map_records_tier_and_chart_without_a_dialog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    store = setup()
    chart = "queued -> sent -> done"
    run(store, tmp_path, [entry("api", tier="multi_step", state_chart=chart), entry("ui")])
    held = {r.kind: r for r in store.records(story_id("api"))}
    assert tiers.from_record(held["tier"].data) == tiers.TIERS[1]
    assert held["state-chart"].data == {"chart": chart}
    assert {r.author for r in held.values()} == {"agent:stories"}
    assert {"tier", "state-chart"}.isdisjoint(decisions.KINDS)
    assert not (tmp_path / "state" / decisions.PENDING).exists()


@pytest.mark.parametrize(
    "over,match",
    [
        ({"tier": "high"}, "entry 0 field tier must be one of concurrency, multi_step, data_rule, none"),
        ({"tier": "concurrency"}, "entry 0 at tier 3 needs a state_chart"),
        ({"state_chart": "a -> b"}, "entry 0 has a state_chart at tier 0; only tiers 2 and 3 take one"),
        ({"after_merge": ["rotate", 3]}, "entry 0 field after_merge must be a list of strings"),
        ({"after_merge": ["rotate", " "]}, "entry 0 field after_merge holds an empty item"),
    ],
)
def test_the_map_refuses_a_bad_tier_chart_or_after_merge_before_any_write(tmp_path: Path, over: dict, match: str):
    store = setup()
    store.writes.clear()
    with pytest.raises(Refusal, match=match):
        run(store, tmp_path, [entry("api", **over)])
    assert store.writes == []
