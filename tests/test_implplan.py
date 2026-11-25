from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from deckhand import implplan, review
from deckhand.records import Record
from deckhand.step import Refusal
from deckhand.steps.implplan import ImplPlan
from deckhand.store.fake import FakeStore
from tests.test_feature_approval import approve_everything, setup

TEAM = ["--team-key", "ACME"]
BASE = {"src/a.py", "src/b.py", "docs/guide.md"}


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    found = tmp_path / "repo"
    (found / ".claude").mkdir(parents=True)
    (found / ".claude" / "deckhand.toml").write_text('repository_label = "widgets"\n')
    return found


def manifest(**over: object) -> dict:
    found: dict = {
        "files": [{"path": "src/a.py", "change": "modify"}, {"path": "src/new.py", "change": "create"}],
        "criteria": [{"text": "an order is queued", "tests": ["tests/test_a.py::test_queue"]}],
        "docs_false": [{"path": "docs/guide.md", "sentence": "Orders are sent at once."}],
        "contract_sites": ["src/a.py"],
        "invariant_sites": [],
    }
    return {**found, **over}


class Git:
    def __init__(self, paths: set[str], head: bool = True) -> None:
        self.paths = paths
        self.head = head
        self.argvs: list[list[str]] = []
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, argv, cwd=None, stdin=None):
        self.argvs.append(argv)
        self.calls.append((argv, cwd))
        if argv[1] == "rev-parse":
            return subprocess.CompletedProcess(argv, 0 if self.head else 128, "", "fatal: bad ref")
        out = "base1\n" if argv[1] == "merge-base" else "\n".join(sorted(self.paths))
        return subprocess.CompletedProcess(argv, 0, out, "")


def files(tmp_path: Path, data: object) -> list[str]:
    plan, found = tmp_path / "plan.md", tmp_path / "manifest.json"
    plan.write_text("# Plan\n\n- [ ] one\n")
    found.write_text(data if isinstance(data, str) else json.dumps(data))
    return ["s1", *TEAM, "--plan", str(plan), "--manifest", str(found)]


def step(store: FakeStore, repo: Path, git: Git | None = None) -> ImplPlan:
    return ImplPlan(store, git or Git(BASE), repo)


def test_criterion_without_test_refuses(tmp_path: Path, repo: Path):
    store = setup()
    approve_everything(store)
    args = files(tmp_path, manifest(criteria=[{"text": "an order is queued", "tests": []}]))
    with pytest.raises(Refusal, match="an order is queued"):
        step(store, repo).apply(args)
    assert [d for d in store._documents.values() if d["title"] == "Implementation plan"] == []
    assert [r for r in store.records("s1") if r.kind == "implplan"] == []


def test_modify_of_missing_file_is_a_check_failure():
    failures = implplan.plan_checks(manifest(files=[{"path": "src/gone.py", "change": "modify"}]), BASE)
    assert failures == ["src/gone.py is to be modified but does not exist at base"]


def test_create_of_existing_file_and_missing_docs_file_fail():
    found = manifest(
        files=[{"path": "src/a.py", "change": "create"}],
        docs_false=[{"path": "docs/none.md", "sentence": "x"}],
    )
    assert implplan.plan_checks(found, BASE) == [
        "src/a.py is to be created but already exists at base",
        "docs/none.md is named as a false doc but does not exist at base",
    ]


def test_a_good_manifest_passes():
    assert implplan.plan_checks(manifest(), BASE) == []


def test_plan_saved_with_manifest_record(tmp_path: Path, repo: Path):
    store = setup()
    approve_everything(store)
    git = Git(BASE)
    args = files(tmp_path, manifest(after_merge=["drop the old queue"]))
    step(store, repo, git).apply(args)
    step(store, repo, git).apply(args)
    docs = [d for d in store._documents.values() if d["title"] == "Implementation plan"]
    assert [d["parent"] for d in docs] == ["s1"]
    assert docs[0]["body"] == "# Plan\n\n- [ ] one\n"
    [record] = [r for r in store.records("s1") if r.kind == "implplan"]
    assert record.subject == "s1"
    assert record.data == manifest(after_merge=["drop the old queue"])
    assert record.version == implplan.version((tmp_path / "plan.md").read_bytes(), record.data)
    assert review.saved_text(tmp_path / "state", "s1", record.version) == "# Plan\n\n- [ ] one\n"
    assert ["git", "merge-base", "HEAD", "origin/HEAD"] in git.argvs
    assert ["git", "ls-tree", "-r", "--name-only", "base1"] in git.argvs


def test_version_changes_with_plan_or_manifest():
    one = implplan.version(b"a", manifest())
    assert one == implplan.version(b"a", manifest())
    assert one != implplan.version(b"b", manifest())
    assert one != implplan.version(b"a", manifest(contract_sites=[]))


def test_refuses_before_feature_approval(tmp_path: Path, repo: Path):
    store = setup()
    with pytest.raises(Refusal, match="planning"):
        step(store, repo).apply(files(tmp_path, manifest()))
    assert [r for r in store.records("s1") if r.kind == "implplan"] == []


def test_refuses_when_no_feature_is_open(tmp_path: Path, repo: Path):
    with pytest.raises(Refusal, match="no feature is open"):
        step(FakeStore(), repo).apply(files(tmp_path, manifest()))


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ("{", "not valid JSON"),
        ([], "must be a JSON object"),
        ({**manifest(), "extra": 1}, "unknown field extra"),
        ({k: v for k, v in manifest().items() if k != "files"}, "field files is missing"),
        (
            manifest(files=[{"path": "a", "change": "create"}, {"path": "b", "change": "rename"}]),
            "files entry 1 field change",
        ),
        (manifest(files=[{"path": "", "change": "create"}]), "files entry 0 field path"),
        (manifest(criteria=[{"text": "t", "tests": "x"}]), "criteria entry 0 field tests"),
        (manifest(docs_false=[{"path": "p"}]), "docs_false entry 0 field sentence"),
        (manifest(contract_sites=["a", 3]), "contract_sites entry 1"),
        (manifest(after_merge="x"), "after_merge must be a list"),
    ],
)
def test_manifest_refusals_name_the_first_bad_entry(tmp_path: Path, data: object, message: str, repo: Path):
    store = setup()
    approve_everything(store)
    with pytest.raises(Refusal, match=message):
        step(store, repo).apply(files(tmp_path, data))
    assert [r for r in store.records("s1") if r.kind == "implplan"] == []


def test_context_names_the_manifest_shape_and_never_fails(capsys: pytest.CaptureFixture[str], repo: Path):
    step(FakeStore(), repo).context(["s1", *TEAM])
    assert "files" in capsys.readouterr().out


def test_missing_origin_head_refuses_with_the_fix(tmp_path: Path, repo: Path):
    store = setup()
    approve_everything(store)
    with pytest.raises(Refusal, match="git remote set-head origin --auto"):
        step(store, repo, Git(BASE, head=False)).apply(files(tmp_path, manifest()))
    assert [r for r in store.records("s1") if r.kind == "implplan"] == []


@pytest.mark.parametrize("name", ["plan", "manifest"])
def test_non_utf8_input_refuses_naming_the_file(tmp_path: Path, name: str, repo: Path):
    store = setup()
    approve_everything(store)
    args = files(tmp_path, manifest())
    (tmp_path / ("plan.md" if name == "plan" else "manifest.json")).write_bytes(b"\xff\xfe bad")
    with pytest.raises(Refusal, match="not valid UTF-8") as caught:
        step(store, repo).apply(args)
    assert (tmp_path / ("plan.md" if name == "plan" else "manifest.json")).name in caught.value.rule


def test_paths_are_normalized_at_parse_time():
    found = implplan.parse_manifest(
        json.dumps(manifest(files=[{"path": "./src/a.py", "change": "modify"}], contract_sites=["./src/a.py"]))
    )
    assert found["files"][0]["path"] == "src/a.py"
    assert found["contract_sites"] == ["src/a.py"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (manifest(files=[{"path": "/etc/x", "change": "create"}]), "files entry 0 field path"),
        (manifest(files=[{"path": "a/../b", "change": "create"}]), "files entry 0 field path"),
        (manifest(docs_false=[{"path": "../x.md", "sentence": "s"}]), "docs_false entry 0 field path"),
        (manifest(contract_sites=["ok", "/abs"]), "contract_sites entry 1"),
        (
            manifest(files=[{"path": "a.py", "change": "create"}, {"path": "./a.py", "change": "modify"}]),
            "files entry 1 field path .* duplicate",
        ),
    ],
)
def test_unusable_paths_refuse(data: object, message: str):
    with pytest.raises(Refusal, match=message):
        implplan.parse_manifest(json.dumps(data))


def test_implplan_refuses_a_plan_that_drops_an_after_merge_item(tmp_path: Path, repo: Path):
    store = setup()
    stub = {"unknowns": [], "new_here": [], "decisions": [], "after_merge": ["rotate the key", "drop the old queue"]}
    store.put_record("s1", Record("stub", "orders", "1", "agent:stories", stub))
    approve_everything(store)
    store.writes.clear()
    with pytest.raises(Refusal, match="the plan drops the after-merge item rotate the key the person approved"):
        step(store, repo).apply(files(tmp_path, manifest(after_merge=["drop the old queue", "Rotate the key"])))
    assert store.writes == []
