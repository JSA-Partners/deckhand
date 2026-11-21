from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from deckhand import answers, launch
from deckhand.cli import main
from deckhand.config import LocalConfig
from deckhand.linear.client import LinearError
from deckhand.linear.workspace import Workspace
from deckhand.statusline import Places
from deckhand.step import Refusal
from deckhand.steps import setup as setup_step
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
        self.project_labels: list[dict] = []
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
        if name == "WorkspaceProjectLabels":
            return {"projectLabels": {"nodes": self.project_labels, "pageInfo": {"hasNextPage": self.more}}}
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
        elif name == "ProjectLabelCreate":
            self.project_labels.append({"id": data["id"], "name": data["name"], "isGroup": False})
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


@pytest.fixture(autouse=True)
def places(tmp_path: Path) -> Places:
    home = tmp_path / "home"
    return Places(
        settings=home / ".claude" / "settings.json",
        managed=tmp_path / "managed",
        project=tmp_path / "project",
        ccstatusline=home / ".config" / "ccstatusline" / "settings.json",
        ccline=home / ".claude" / "ccline" / "config.toml",
        config=Path(os.environ["DECKHAND_CONFIG_DIR"]),
        root=tmp_path / "plugin",
    )


@pytest.fixture(autouse=True)
def isolated_places(monkeypatch: pytest.MonkeyPatch, places: Places) -> None:
    monkeypatch.setattr(Places, "here", classmethod(lambda cls: places))


def make(linear: FakeLinear) -> Setup:
    return Setup(workspace=Workspace(linear))


ARGS = ["--team-key", "ACME", "--repository", "widgets=acme/widgets"]


def test_apply_twice_writes_once(capsys: pytest.CaptureFixture[str]):
    linear = FakeLinear("ACME")
    setup = make(linear)
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
    setup = make(linear)
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
    setup = make(linear)
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
    setup = make(linear)
    setup.context(ARGS)
    out = capsys.readouterr().out
    assert linear.writes == []
    assert "Triage" in out
    assert "create state In Progress" in out
    assert "create label group Repository" in out
    assert "acme/widgets" in out and "auto_merge" not in out
    config_dir = Path(os.environ["DECKHAND_CONFIG_DIR"])
    assert f"would write {config_dir / 'config.toml'}" in out
    assert not config_dir.exists()


def test_refuses_without_team_key():
    linear = FakeLinear("ACME")
    setup = make(linear)
    with pytest.raises(Refusal):
        setup.apply(["--repository", "widgets=acme/widgets"])
    assert linear.writes == []


def test_context_without_team_key_reads_it_from_config(capsys: pytest.CaptureFixture[str]):
    config_dir = Path(os.environ["DECKHAND_CONFIG_DIR"])
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('team_key = "ACME"\n')
    linear = FakeLinear("ACME")
    make(linear).context(["--repository", "widgets=acme/widgets"])
    out = capsys.readouterr().out
    assert "team ACME: found" in out
    assert "acme/widgets" in out
    assert linear.writes == []


def test_context_without_any_team_key_prints_what_setup_needs(capsys: pytest.CaptureFixture[str]):
    linear = FakeLinear("ACME")
    make(linear).context([])
    out = capsys.readouterr().out
    assert "--team-key KEY" in out
    assert "found" not in out
    assert linear.writes == []
    assert not Path(os.environ["DECKHAND_CONFIG_DIR"]).exists()


def test_refuses_unknown_team_before_any_write():
    linear = FakeLinear("ACME")
    setup = make(linear)
    with pytest.raises(Refusal, match="NOPE"):
        setup.apply(["--team-key", "NOPE"])
    assert linear.writes == []


def test_refuses_malformed_repository_before_any_write():
    linear = FakeLinear("ACME")
    setup = make(linear)
    with pytest.raises(Refusal):
        setup.apply(["--team-key", "ACME", "--repository", "widgets"])
    assert linear.writes == []


@pytest.mark.parametrize("verb", ["context", "apply"])
def test_setup_has_no_auto_merge_option(verb: str):
    linear = FakeLinear("ACME")
    setup = make(linear)
    with pytest.raises(Refusal, match="--enable-auto-merge"):
        getattr(setup, verb)([*ARGS, "--enable-auto-merge"])
    assert linear.writes == []


def test_setup_ensures_the_workspace_project_label_once():
    linear = FakeLinear("ACME", "ZED")
    setup = make(linear)
    setup.apply(ARGS)
    setup.apply(["--team-key", "ZED"])
    created = [w[1]["input"] for w in linear.writes if w[0] == "ProjectLabelCreate"]
    assert [row["name"] for row in created] == ["deckhand"]
    assert "teamId" not in created[0]


def test_wrong_automation_state_is_updated_not_created():
    linear = FakeLinear("ACME", states=[(n, "started") for n in ("In Progress", "In Review", "Done", "Backlog")])
    linear.teams["ACME"]["automation"] = [{"id": "a-merge", "event": "merge", "state": {"name": "In Review"}}]
    setup = make(linear)
    setup.apply(ARGS)
    names = [w[0] for w in linear.writes]
    assert "GitAutomationStateUpdate" in names
    assert [w[1]["input"]["event"] for w in linear.writes if w[0] == "GitAutomationStateCreate"] == ["draft", "start"]
    update = next(w for w in linear.writes if w[0] == "GitAutomationStateUpdate")
    assert update[1] == {"id": "a-merge", "input": {"stateId": "s-Done"}}


def test_rerun_after_partial_apply_writes_only_what_is_missing():
    linear = FakeLinear("ACME")
    setup = make(linear)
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
    setup = make(linear)
    setup.context(["--team-key", "NOPE"])
    assert "no team has this key" in capsys.readouterr().out
    assert linear.writes == []


def test_team_past_one_page_refuses():
    linear = FakeLinear("ACME")
    linear.more = True
    setup = make(linear)
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
    setup = make(linear)
    setup.context([*ARGS, "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert linear.writes == []
    assert facts["team"] == {"key": "ACME", "found": True}
    assert facts["states"] == [
        {"name": "Triage", "type": "triage", "designed": False},
        {"name": "Done", "type": "completed", "designed": True},
    ]
    assert facts["automation"] == [{"event": "merge", "state": "Done"}]
    assert facts["labels"] == ["Bug"]
    assert facts["repositories"] == [{"label": "widgets", "repository": "acme/widgets"}]
    assert "create state In Progress" in facts["planned_changes"]
    assert "create label widgets under Repository" in facts["planned_changes"]


def test_context_json_for_a_missing_team(capsys: pytest.CaptureFixture[str]):
    setup = make(FakeLinear("ACME"))
    setup.context(["--team-key", "NOPE", "--json"])
    facts = json.loads(capsys.readouterr().out)
    assert facts["team"] == {"key": "NOPE", "found": False}
    assert facts["statusline"]["owner"] == "none"


def test_apply_refuses_json():
    linear = FakeLinear("ACME")
    setup = make(linear)
    with pytest.raises(Refusal, match="--json"):
        setup.apply([*ARGS, "--json"])
    assert linear.writes == []


def local_files(config_dir: Path) -> dict[str, tuple[int, str]]:
    return {
        name: ((config_dir / name).stat().st_mtime_ns, (config_dir / name).read_text())
        for name in ("config.toml", "captain-settings.json")
    }


def test_setup_writes_config_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    config_dir = Path(os.environ["DECKHAND_CONFIG_DIR"])
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('sandbox_write = ["~/.cache/uv"]\n\n[clones]\ngadgets = "/elsewhere"\n')
    clone = tmp_path / "widgets"
    clone.mkdir()
    setup = make(FakeLinear("ACME"))
    args = [*ARGS, "--clone", f"widgets={clone}", "--slot-cap", "3"]
    setup.apply(args)
    out = capsys.readouterr().out
    assert LocalConfig.load(config_dir / "config.toml") == LocalConfig(
        clones={"gadgets": Path("/elsewhere"), "widgets": clone},
        slot_cap=3,
        team_key="ACME",
        sandbox_write=["~/.cache/uv"],
    )
    captain = json.loads((config_dir / "captain-settings.json").read_text())
    assert captain == launch.captain_settings(launch.protected(config_dir, answers.state_dir()))
    launch_line = f'claude --settings {config_dir / "captain-settings.json"} --name captain "/deckhand:captain"'
    assert out.splitlines()[-1] == launch_line
    assert f"wrote {config_dir / 'config.toml'}" in out
    first = local_files(config_dir)
    setup.apply(args)
    out = capsys.readouterr().out
    assert local_files(config_dir) == first
    assert "wrote" not in out
    assert out.splitlines()[-1] == launch_line


@pytest.mark.parametrize(
    ("extra", "rule"),
    [
        (["--clone", "widgets=/no/such/clone"], "--clone LABEL=PATH needs a directory"),
        (["--clone", "widgets"], "--clone LABEL=PATH needs a directory"),
        (["--slot-cap", "0"], "--slot-cap takes a positive integer"),
        (["--slot-cap", "two"], "--slot-cap takes a positive integer"),
    ],
)
def test_setup_refuses_bad_local_flags_before_any_write(extra: list[str], rule: str):
    linear = FakeLinear("ACME")
    setup = make(linear)
    with pytest.raises(Refusal, match=rule):
        setup.apply([*ARGS, *extra])
    assert linear.writes == []
    assert not Path(os.environ["DECKHAND_CONFIG_DIR"]).exists()


def test_parse_returns_every_flag(tmp_path: Path):
    parsed = setup_step._parse(["--team-key", "ACME", "--clone", f"widgets={tmp_path}", "--slot-cap", "2"])
    assert parsed == {
        "team_key": "ACME",
        "repositories": {},
        "clones": {"widgets": tmp_path},
        "slot_cap": 2,
        "statusline": None,
    }


def test_statusline_apply_installs_without_linear(places: Places, capsys: pytest.CaptureFixture[str]):
    linear = FakeLinear("ACME")
    make(linear).apply(["--statusline"])
    out = capsys.readouterr().out
    assert linear.writes == []
    line = json.loads(places.settings.read_text())["statusLine"]
    assert line["command"].endswith("statusline-segment --color")
    assert f"wrote {places.settings}" in out
    assert "launch" not in out and "claude --settings" not in out


def test_statusline_remove_restores_the_settings(places: Places, capsys: pytest.CaptureFixture[str]):
    places.settings.parent.mkdir(parents=True)
    places.settings.write_bytes(b'{"statusLine": {"type": "command", "command": "printf mine"}}')
    setup = make(FakeLinear("ACME"))
    setup.apply(["--statusline"])
    setup.apply(["--statusline-remove"])
    assert places.settings.read_bytes() == b'{"statusLine": {"type": "command", "command": "printf mine"}}'
    assert f"restored {places.settings}" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["--statusline", "--team-key", "ACME"],
        ["--team-key", "ACME", "--statusline-remove"],
        ["--statusline", "--statusline-remove"],
    ],
)
def test_statusline_takes_no_other_flag(places: Places, args: list[str]):
    linear = FakeLinear("ACME")
    with pytest.raises(Refusal, match="--statusline"):
        make(linear).apply(args)
    assert linear.writes == []
    assert not places.settings.exists()


def test_context_reports_the_status_line_even_without_a_team(places: Places, capsys: pytest.CaptureFixture[str]):
    places.settings.parent.mkdir(parents=True)
    places.settings.write_text('{"statusLine": {"type": "command", "command": "npx -y ccstatusline@latest"}}')
    make(FakeLinear("ACME")).context([])
    out = capsys.readouterr().out
    assert "--team-key KEY" in out
    assert "status line: ccstatusline" in out
    assert "wrapper" in out


def test_context_statusline_alone_skips_linear(places: Places, capsys: pytest.CaptureFixture[str]):
    Setup(workspace=None).context(["--statusline"])
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "status line: none"
    assert f"would write {places.settings}" in out
    assert not places.settings.exists()


def test_context_never_fails_on_a_broken_settings_file(places: Places, capsys: pytest.CaptureFixture[str]):
    places.settings.parent.mkdir(parents=True)
    places.settings.write_text("{broken")
    make(FakeLinear("ACME")).context(["--statusline"])
    out = capsys.readouterr().out
    assert "status line: unknown" in out
    assert "not valid JSON" in out


def test_context_statusline_with_other_flags_prints_the_usage(capsys: pytest.CaptureFixture[str]):
    Setup(workspace=None).context(["--statusline", "--team-key", "ACME"])
    out = capsys.readouterr().out
    assert "--statusline takes no other flag" in out
    assert "status line: none" in out
