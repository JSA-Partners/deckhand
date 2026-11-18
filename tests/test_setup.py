from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.request

import pytest

from deckhand.cli import main
from deckhand.github import Github
from deckhand.linear.client import LinearError
from deckhand.linear.workspace import Workspace
from deckhand.step import Refusal
from deckhand.steps.setup import Setup


class FakeLinear:
    def __init__(self, *keys: str, states: list[tuple[str, str]] | None = None) -> None:
        self.teams = {
            key: {
                "id": f"team-{key}",
                "key": key,
                "states": [{"id": f"s-{name}", "name": name, "type": kind} for name, kind in (states or [])],
                "automation": [],
                "labels": [],
                "autoArchivePeriod": 6.0,
                "autoCloseParentIssues": False,
            }
            for key in keys
        }
        self.writes: list[tuple[str, dict]] = []
        self.fail_after: int | None = None
        self.more = False
        self.reads = {
            "TeamStates": ("states", "states"),
            "TeamLabels": ("labels", "labels"),
            "TeamAutomation": ("gitAutomationStates", "automation"),
        }

    def _team(self, team_id: str) -> dict:
        return next(team for team in self.teams.values() if team["id"] == team_id)

    def query(self, document: str, variables: dict) -> dict:
        name = re.search(r"(?:query|mutation) (\w+)", document).group(1)
        if name == "TeamByKey":
            found = self.teams.get(variables["key"])
            if found is None:
                return {"teams": {"nodes": []}}
            keep = ("id", "key", "autoArchivePeriod", "autoCloseParentIssues")
            return {"teams": {"nodes": [{k: found[k] for k in keep}]}}
        if name in self.reads:
            field, source = self.reads[name]
            rows = self._team(variables["id"])[source]
            return {"team": {field: {"nodes": rows, "pageInfo": {"hasNextPage": self.more}}}}
        if self.fail_after is not None and len(self.writes) >= self.fail_after:
            raise LinearError("network", "could not reach Linear")
        self.writes.append((name, variables))
        data = variables.get("input", {})
        if name == "WorkflowStateCreate":
            team = self._team(data["teamId"])
            team["states"].append({"id": f"s-{data['name']}", "name": data["name"], "type": data["type"]})
        elif name == "IssueLabelCreate":
            team = self._team(data["teamId"])
            parent = next((x for x in team["labels"] if x["id"] == data.get("parentId")), None)
            team["labels"].append(
                {
                    "id": data["id"],
                    "name": data["name"],
                    "isGroup": data.get("isGroup", False),
                    "parent": {"name": parent["name"]} if parent else None,
                }
            )
        elif name == "GitAutomationStateCreate":
            team = self._team(data["teamId"])
            state = next(x for x in team["states"] if x["id"] == data["stateId"])
            team["automation"].append(
                {"id": f"a-{data['event']}", "event": data["event"], "state": {"name": state["name"]}}
            )
        elif name == "GitAutomationStateUpdate":
            for team in self.teams.values():
                for row in team["automation"]:
                    if row["id"] == variables["id"]:
                        row["state"] = {"name": next(x for x in team["states"] if x["id"] == data["stateId"])["name"]}
        elif name == "TeamUpdate":
            team = self._team(variables["id"])
            team["autoArchivePeriod"] = data["autoArchivePeriod"]
            team["autoCloseParentIssues"] = data["autoCloseParentIssues"]
        return {name[0].lower() + name[1:]: {"success": True}}


class FakeRunner:
    def __init__(self, current: str = "false") -> None:
        self.current = current
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if "PATCH" in args:
            self.current = "true"
            return ""
        return self.current


def make(linear: FakeLinear, runner: FakeRunner | None = None) -> tuple[Setup, FakeRunner]:
    runner = runner or FakeRunner()
    return Setup(workspace=Workspace(linear), github=Github(runner)), runner


ARGS = ["--team-key", "ACME", "--repository", "widgets=acme/widgets"]


def test_apply_twice_writes_once(capsys: pytest.CaptureFixture[str]):
    linear = FakeLinear("ACME")
    setup, _ = make(linear)
    setup.apply(ARGS)
    first = len(linear.writes)
    assert first > 0
    capsys.readouterr()
    setup.apply(ARGS)
    assert len(linear.writes) == first
    assert "no changes" in capsys.readouterr().out


@pytest.mark.parametrize("key", ["ACME", "ZED"])
def test_any_team_key(key: str):
    linear = FakeLinear("ACME", "ZED")
    setup, _ = make(linear)
    setup.apply(["--team-key", key, "--repository", "widgets=acme/widgets"])
    other = "ZED" if key == "ACME" else "ACME"
    assert linear.teams[key]["autoCloseParentIssues"] is True
    assert linear.teams[key]["autoArchivePeriod"] == 1
    assert {x["name"] for x in linear.teams[key]["labels"]} == {"Repository", "widgets"}
    assert linear.teams[other]["labels"] == []
    assert linear.teams[other]["autoArchivePeriod"] == 6.0
    events = {row["event"]: row["state"]["name"] for row in linear.teams[key]["automation"]}
    assert events == {"draft": "In Progress", "start": "In Review", "merge": "Done"}


def test_existing_states_kept_and_missing_added():
    linear = FakeLinear("ACME", states=[("Backlog", "backlog"), ("Triage", "triage"), ("Done", "completed")])
    setup, _ = make(linear)
    setup.apply(ARGS)
    names = [state["name"] for state in linear.teams["ACME"]["states"]]
    assert names[:3] == ["Backlog", "Triage", "Done"]
    assert sorted(names[3:]) == ["Canceled", "Duplicate", "In Progress", "In Review"]
    created = {w[1]["input"]["name"]: w[1]["input"]["type"] for w in linear.writes if w[0] == "WorkflowStateCreate"}
    assert created == {
        "In Progress": "started",
        "In Review": "started",
        "Canceled": "canceled",
        "Duplicate": "duplicate",
    }


def test_context_lists_planned_changes_without_writing(capsys: pytest.CaptureFixture[str]):
    linear = FakeLinear("ACME", states=[("Triage", "triage")])
    setup, runner = make(linear)
    setup.context(ARGS)
    out = capsys.readouterr().out
    assert linear.writes == []
    assert not any("PATCH" in call for call in runner.calls)
    assert "Triage" in out
    assert "create state In Progress" in out
    assert "create label group Repository" in out
    assert "acme/widgets" in out and "allow_auto_merge" in out


def test_refuses_without_team_key():
    linear = FakeLinear("ACME")
    setup, _ = make(linear)
    with pytest.raises(Refusal):
        setup.apply(["--repository", "widgets=acme/widgets"])
    assert linear.writes == []


def test_refuses_unknown_team_before_any_write():
    linear = FakeLinear("ACME")
    setup, _ = make(linear)
    with pytest.raises(Refusal, match="NOPE"):
        setup.apply(["--team-key", "NOPE"])
    assert linear.writes == []


def test_refuses_malformed_repository_before_any_write():
    linear = FakeLinear("ACME")
    setup, _ = make(linear)
    with pytest.raises(Refusal):
        setup.apply(["--team-key", "ACME", "--repository", "widgets"])
    assert linear.writes == []


def test_auto_merge_only_with_flag():
    linear = FakeLinear("ACME")
    setup, runner = make(linear)
    setup.apply(ARGS)
    assert not any("PATCH" in call for call in runner.calls)
    setup.apply([*ARGS, "--enable-auto-merge"])
    patches = [call for call in runner.calls if "PATCH" in call]
    assert patches == [["api", "-X", "PATCH", "repos/acme/widgets", "-F", "allow_auto_merge=true"]]
    setup.apply([*ARGS, "--enable-auto-merge"])
    assert len([call for call in runner.calls if "PATCH" in call]) == 1


def test_wrong_automation_state_is_updated_not_created():
    linear = FakeLinear("ACME", states=[(n, "started") for n in ("In Progress", "In Review", "Done", "Backlog")])
    linear.teams["ACME"]["automation"] = [{"id": "a-merge", "event": "merge", "state": {"name": "In Review"}}]
    setup, _ = make(linear)
    setup.apply(ARGS)
    names = [w[0] for w in linear.writes]
    assert "GitAutomationStateUpdate" in names
    assert [w[1]["input"]["event"] for w in linear.writes if w[0] == "GitAutomationStateCreate"] == ["draft", "start"]
    update = next(w for w in linear.writes if w[0] == "GitAutomationStateUpdate")
    assert update[1] == {"id": "a-merge", "input": {"stateId": "s-Done"}}


def test_rerun_after_partial_apply_writes_only_what_is_missing():
    linear = FakeLinear("ACME")
    setup, _ = make(linear)
    linear.fail_after = 3
    with pytest.raises(LinearError):
        setup.apply(ARGS)
    done = list(linear.writes)
    assert len(done) == 3
    linear.fail_after = None
    setup.apply(ARGS)
    rest = linear.writes[3:]
    assert all(w not in done for w in rest)
    before = len(linear.writes)
    setup.apply(ARGS)
    assert len(linear.writes) == before
    assert len(linear.teams["ACME"]["states"]) == 6


def test_context_without_team_prints_message_and_does_not_raise(capsys: pytest.CaptureFixture[str]):
    linear = FakeLinear("ACME")
    setup, _ = make(linear)
    setup.context(["--team-key", "NOPE"])
    assert "no team has this key" in capsys.readouterr().out
    assert linear.writes == []


def test_team_past_one_page_refuses():
    linear = FakeLinear("ACME")
    linear.more = True
    setup, _ = make(linear)
    with pytest.raises(Refusal, match="more than"):
        setup.apply(ARGS)
    assert linear.writes == []


KEY = "lin_api_personalvalue"


@pytest.fixture
def through_main(tmp_path, monkeypatch: pytest.MonkeyPatch):
    from deckhand.step import REGISTRY

    config = tmp_path / "deckhand"
    config.mkdir()
    (config / "linear-app.json").write_text('{"client_id": "cid", "client_secret": "csecret"}')
    (config / "linear-personal.key").write_text(KEY)
    for path in config.iterdir():
        path.chmod(0o600)
    monkeypatch.setenv("DECKHAND_CONFIG_DIR", str(config))
    monkeypatch.setitem(REGISTRY, "setup", Setup())
    requests: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> object:
        requests.append(request)
        raise urllib.error.HTTPError(request.full_url, 401, "x", {}, io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return config, requests


def test_revoked_key_refuses_through_main_naming_the_key_file(through_main, capsys: pytest.CaptureFixture[str]):
    config, requests = through_main
    assert main(["setup", "apply", "--team-key", "ACME"]) == 1
    captured = capsys.readouterr()
    assert len(requests) == 2
    assert captured.err == f"deckhand: unauthorized: check {config / 'linear-personal.key'}\n"
    assert KEY not in captured.out + captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("verb", ["context", "apply"])
@pytest.mark.parametrize("raw", [KEY.encode() + b"\xff", KEY.encode() + b"\nlin_api_second"])
def test_unusable_key_never_reaches_the_output(through_main, capsys: pytest.CaptureFixture[str], verb, raw):
    config, requests = through_main
    (config / "linear-personal.key").write_bytes(raw)
    main(["setup", verb, "--team-key", "ACME"])
    captured = capsys.readouterr()
    assert requests == []
    assert "lin_api" not in captured.out + captured.err
    assert "Traceback" not in captured.err


def test_context_json_prints_the_same_facts(capsys: pytest.CaptureFixture[str]):
    linear = FakeLinear("ACME", states=[("Triage", "triage"), ("Done", "completed")])
    linear.teams["ACME"]["automation"] = [{"id": "a-merge", "event": "merge", "state": {"name": "Done"}}]
    linear.teams["ACME"]["labels"] = [{"id": "l1", "name": "Bug", "isGroup": False, "parent": None}]
    setup, runner = make(linear, FakeRunner("true"))
    setup.context([*ARGS, "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert linear.writes == []
    assert not any("PATCH" in call for call in runner.calls)
    assert facts["team"] == {"key": "ACME", "found": True}
    assert facts["states"] == [
        {"name": "Triage", "type": "triage", "designed": False},
        {"name": "Done", "type": "completed", "designed": True},
    ]
    assert facts["automation"] == [{"event": "merge", "state": "Done"}]
    assert facts["labels"] == ["Bug"]
    assert facts["repositories"] == [{"label": "widgets", "repository": "acme/widgets", "allow_auto_merge": True}]
    assert "create state In Progress" in facts["planned_changes"]
    assert "create label widgets under Repository" in facts["planned_changes"]


def test_context_json_for_a_missing_team(capsys: pytest.CaptureFixture[str]):
    setup, _ = make(FakeLinear("ACME"))
    setup.context(["--team-key", "NOPE", "--json"])
    assert json.loads(capsys.readouterr().out) == {"team": {"key": "NOPE", "found": False}}


def test_apply_refuses_json():
    linear = FakeLinear("ACME")
    setup, _ = make(linear)
    with pytest.raises(Refusal, match="--json"):
        setup.apply([*ARGS, "--json"])
    assert linear.writes == []
