from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from deckhand import features, planning, versions
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
    }
    return {**found, **over}


def setup() -> FakeStore:
    store = FakeStore()
    store.add_repository_label("ACME", "widgets")
    store.create_project(P1, "ACME", "Alpha", "")
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
    [stub] = store.records(story_id("api"))
    assert (stub.kind, stub.subject) == ("stub", "api")
    assert stub.data == {"unknowns": ["rate limits"], "new_here": ["queue"], "decisions": []}


def test_map_keeps_expected_decisions_in_the_stub_and_reruns_write_nothing(tmp_path: Path):
    store = setup()
    entries = [entry("api", decisions=["Keep orders in one table"])]
    run(store, tmp_path, entries)
    [stub] = store.records(story_id("api"))
    assert stub.data["decisions"] == ["Keep orders in one table"]
    store.writes.clear()
    run(store, tmp_path, entries)
    assert store.writes == []


@pytest.mark.parametrize("bad", ["Keep it", ["Keep it", 3]])
def test_map_refuses_a_malformed_decisions_field(tmp_path: Path, bad: object):
    store = setup()
    store.writes.clear()
    with pytest.raises(Refusal, match="entry 1 field decisions must be a list of strings"):
        run(store, tmp_path, [entry("api"), entry("ui", decisions=bad)])
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
    assert [w[0] for w in store.writes] == ["set_issue_text"]


def test_map_refuses_cycle(tmp_path: Path):
    store = setup()
    store.writes.clear()
    with pytest.raises(Refusal, match="cycle"):
        run(store, tmp_path, [entry("a", depends_on=["b"]), entry("b", depends_on=["a"])])
    assert store.writes == []


def test_map_refuses_unknown_repository(tmp_path: Path):
    store = setup()
    store.writes.clear()
    with pytest.raises(Refusal, match="gadgets"):
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
    assert features.phase(features.open_feature(store, "ACME")) is features.Phase.PLANNING
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
