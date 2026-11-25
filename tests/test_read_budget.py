from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from deckhand import answers, implplan, reading, signoff
from deckhand.config import LocalConfig
from deckhand.features import FEATURE_LABEL
from deckhand.fleet import Fleet
from deckhand.ops import create_id
from deckhand.records import Record
from deckhand.steps.after import After
from deckhand.steps.build import Build
from deckhand.steps.decide import Decide
from deckhand.steps.hand import Hand
from deckhand.steps.merge import Merge
from deckhand.steps.review import Review
from deckhand.steps.signoff import Signoff
from deckhand.steps.stories import Stories
from deckhand.store import open as store_open
from deckhand.store.cached import READS, CachedStore
from deckhand.store.fake import FakeStore
from tests.conftest import CountingStore
from tests.fake_graphql import FakeGraphQL
from tests.shell import Shell
from tests.test_feature_approval import P1, load, mapped, reapprove, reviewed, setup
from tests.test_implplan import BASE, Git, manifest, repo
from tests.test_step_after import chored
from tests.test_step_build import Shell as BuildShell
from tests.test_step_hand import TEAM, clone
from tests.test_step_review import planned, state, submit
from tests.test_step_signoff import Gh, ready
from tests.test_step_stories import entry
from tests.test_step_stories import setup as unmapped

__all__ = ["clone", "repo", "state"]

BOUND = 10
DAY = date(2026, 11, 17)

Serve = Callable[[FakeStore], dict[str, FakeGraphQL]]


def released(store: FakeStore, name: str, start: str, target: str | None = None) -> None:
    id = create_id(f"ACME:{name}", "feature")
    store.create_project(id, "ACME", name, "", [FEATURE_LABEL])
    store.set_project_dates(id, start, target)
    for n in range(5):
        store.create_issue(f"{name.lower()}{n}", "ACME", f"{name} {n}", "", id, ["widgets"])
        store.set_issue_state(f"{name.lower()}{n}", "Done")


def with_released(store: FakeStore) -> FakeStore:
    for name, start in (("Beta", "2026-10-19"), ("Gamma", "2026-10-26"), ("Delta", "2026-11-02")):
        released(store, name, start)
    return store


def alpha(stories: int) -> FakeStore:
    store = setup()
    for n in range(3, stories + 1):
        store.create_issue(f"s{n}", "ACME", f"Story {n}", f"Outcome: {n}.", P1, ["widgets"])
    for n in range(1, stories):
        store.relate(f"s{n}", f"s{n + 1}")
    reviewed(store)
    for story in load(store).live:
        mapped(store, story["id"])
    reapprove(store)
    return with_released(store)


@pytest.fixture
def team(tmp_path: Path, repo: Path) -> FakeStore:
    return with_released(planned(tmp_path, repo))


@pytest.fixture
def opened(
    team: FakeStore, monkeypatch: pytest.MonkeyPatch, counting_store: Callable[[FakeStore], CountingStore]
) -> dict[str, CountingStore]:
    found = {"app": counting_store(team), "person": counting_store(team)}

    def open_store(role: str, teams: dict[str, str] | None = None) -> CachedStore:
        found[role].opens += 1
        return CachedStore(found[role])

    monkeypatch.setattr(store_open, "open_store", open_store)
    return found


@pytest.fixture
def linear(monkeypatch: pytest.MonkeyPatch) -> Serve:
    def serve(store: FakeStore) -> dict[str, FakeGraphQL]:
        clients = {"app": FakeGraphQL(store), "person": FakeGraphQL(store)}
        monkeypatch.setattr(store_open.Credentials, "load", staticmethod(lambda folder: "credentials"))
        monkeypatch.setattr(store_open, "connect", lambda credentials, role, meter: clients[role])
        monkeypatch.setattr(store_open, "LocalRecords", lambda path: store)
        return clients

    return serve


def queries(clients: dict[str, FakeGraphQL]) -> list[str]:
    return [name for client in clients.values() for name in client.queries]


def reads(counting: CountingStore) -> list[tuple]:
    return [call for call in counting.log if call[0] in READS]


def repeated(counting: CountingStore) -> list[tuple]:
    return [call for call, n in Counter(reads(counting)).items() if n > 1]


def engine_read() -> reading.Reading:
    return reading.read(
        store_open.open_store("app"), lambda: store_open.open_store("person"), "ACME", Fleet(), LocalConfig(), DAY
    )


def run_read() -> None:
    found = engine_read()
    assert (found.facts["feature"], [row["ref"] for row in found.facts["order"]]) == ("Alpha", ["ACME-1", "ACME-2"])


def test_the_engine_read_reads_each_linear_object_at_most_once(clone: Path, opened: dict[str, CountingStore]):
    run_read()
    assert repeated(opened["app"]) == []
    assert reads(opened["person"]) == [("viewer", (), ())]


def test_build_review_and_decide_contexts_read_each_object_at_most_once(
    repo: Path, opened: dict[str, CountingStore], capsys: pytest.CaptureFixture[str]
):
    runs = {
        "build": lambda: Build(None, BuildShell(), repo).context(["ACME-1", *TEAM, "--json"]),
        "review": lambda: Review(None, Git(BASE), repo).context(["ACME-1", "--stage", "plan", *TEAM, "--json"]),
        "decide": lambda: Decide(None).context(["lean", "ACME-1", *TEAM, "--json"]),
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
    assert opened["person"].opens == 0


def test_build_context_with_four_stories_stays_under_ten_requests(
    tmp_path: Path, repo: Path, linear: Serve, capsys: pytest.CaptureFixture[str]
):
    store = alpha(4)
    submit(store, tmp_path, repo)
    capsys.readouterr()
    served = linear(store)
    Build(None, BuildShell(), repo).context(["ACME-1", *TEAM, "--json"])
    assert json.loads(capsys.readouterr().out)["story"] == "ACME-1"
    assert len(queries(served)) < BOUND
    assert "IssueRelations" not in queries(served)


def test_engine_read_with_no_open_feature_loads_one_feature(linear: Serve):
    store = FakeStore()
    for n in range(6):
        released(store, f"F{n}", f"2026-10-{10 + n:02d}", f"2026-10-{20 + n:02d}" if n % 2 else None)
    served = linear(store)
    found = engine_read()
    assert found.facts["feature"] == "F5"
    assert queries(served).count("ProjectIssues") == 1
    assert len(queries(served)) < BOUND


def test_one_chore_candidate_adds_at_most_two_requests(linear: Serve):
    store = alpha(2)
    served = linear(store)
    engine_read()
    before = len(queries(served))
    plan = manifest(after_merge=["rotate the webhook secret"])
    store.put_record("beta0", Record(implplan.PURPOSE, "beta0", "p1", "agent:implplan", plan))
    store.put_record("beta0", Record(signoff.KIND, "beta0", "c1:digest", "person:sess", {"answer": "Sign off"}))
    served = linear(store)
    found = engine_read()
    assert [f.project["name"] for f in found.chores] == ["Beta"]
    assert len(queries(served)) <= before + 2, Counter(queries(served))


def test_request_count_is_constant_in_story_count(linear: Serve):
    counts = []
    for stories in (2, 6):
        served = linear(alpha(stories))
        assert len(engine_read().facts["order"]) == stories
        counts.append(len(queries(served)))
    assert counts[0] == counts[1]


def _signed(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[FakeStore, Callable[[], None]]:
    state = answers.state_dir()
    store = ready(tmp_path, repo, state, capsys)
    capsys.readouterr()
    Decide(store, runner=BuildShell(), repo=repo).context(["sign-off", "ACME-1", *TEAM, "--json"])
    asked = json.loads(capsys.readouterr().out)
    question = asked["tool_input"]["questions"][0]
    picked = {"questions": [question], "answers": {question["question"]: "Sign off"}}
    answers.record({"session_id": "sess", "tool_response": picked}, state)
    return store, lambda: Decide(None, runner=BuildShell(), repo=repo).apply([asked["nonce"], *TEAM])


def _done(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[FakeStore, Callable[[], None]]:
    store = chored()
    store.set_issue_state("s1", "Done")
    return store, lambda: After(None).context(["ACME-1", *TEAM, "--json"])


def _ready(run: Callable[[Path], None]) -> Callable[..., tuple[FakeStore, Callable[[], None]]]:
    def made(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[FakeStore, Callable[[], None]]:
        return ready(tmp_path, repo, answers.state_dir(), capsys), lambda: run(repo)

    return made


def _alpha(run: Callable[[Path], None]) -> Callable[..., tuple[FakeStore, Callable[[], None]]]:
    def made(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[FakeStore, Callable[[], None]]:
        store = alpha(4)
        submit(store, tmp_path, repo)
        return store, lambda: run(repo)

    return made


COMMANDS = {
    "engine read": _alpha(lambda repo: engine_read()),
    "build context": _alpha(lambda repo: Build(None, BuildShell(), repo).context(["ACME-1", *TEAM])),
    "review context": _alpha(
        lambda repo: Review(None, Git(BASE), repo).context(["ACME-1", "--stage", "plan", *TEAM, "--json"])
    ),
    "decide context": _alpha(lambda repo: Decide(None).context(["lean", "ACME-1", *TEAM, "--json"])),
    "hand context": _alpha(lambda repo: Hand(None, Shell()).context([*TEAM, "--json"])),
    "decide apply on a sign-off": _signed,
    "signoff context": _ready(lambda repo: Signoff(None, BuildShell(), repo).context(["ACME-1", *TEAM, "--json"])),
    "merge context": _ready(lambda repo: Merge(None, Gh(), repo).context(["ACME-1", *TEAM])),
    "after context": _done,
}


@pytest.mark.parametrize("command", COMMANDS)
def test_every_measured_command_stays_under_ten_queries(
    command: str, tmp_path: Path, repo: Path, linear: Serve, capsys: pytest.CaptureFixture[str]
):
    store, run = COMMANDS[command](tmp_path, repo, capsys)
    capsys.readouterr()
    served = linear(store)
    run()
    assert len(queries(served)) < BOUND, Counter(queries(served))
    if command == "decide apply on a sign-off":
        assert capsys.readouterr().out.startswith("done: recorded sign-off")


# Exempt from the bound: every write clears the memo, so each story's ensure reads follow its own write.
def test_stories_apply_reads_a_fixed_six_plus_three_per_new_story(tmp_path: Path, linear: Serve):
    keys = (("a", []), ("b", ["a"]), ("c", ["b"]), ("d", ["a"]))
    path = tmp_path / "map.json"
    path.write_text(json.dumps([entry(key, depends_on=deps) for key, deps in keys]))
    store = unmapped()
    served = linear(store)
    Stories(None).apply([*TEAM, "--file", str(path)])
    assert len(queries(served)) <= 6 + 3 * len(keys)
    assert "IssueRelations" not in queries(served)
    served = linear(store)
    Stories(None).apply([*TEAM, "--file", str(path)])
    assert len(queries(served)) <= 6 + len(keys)
