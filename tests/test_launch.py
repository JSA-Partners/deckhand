from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from deckhand import git, launch
from deckhand.config import LocalConfig
from deckhand.policy import Policy
from deckhand.step import Refusal

CLAUDE_CONFIG = (
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/skills/**",
    ".claude/agents/**",
    ".claude/commands/**",
    ".claude/hooks/**",
    ".claude/workflows/**",
    ".claude/scheduled_tasks.json",
    ".mcp.json",
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = (tmp_path / "home").resolve()
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    return path


def linked(tmp_path: Path, name: str) -> tuple[Path, Path]:
    real = (tmp_path / f"real-{name}").resolve()
    real.mkdir()
    link = tmp_path / name
    link.symlink_to(real)
    return link, real


def rule(tool: str, path: Path, suffix: str = "") -> str:
    return f"{tool}(//{str(path).lstrip('/')}{suffix})"


@pytest.fixture
def layout(tmp_path: Path, home: Path) -> dict:
    config, config_real = linked(tmp_path, "config")
    state, state_real = linked(tmp_path, "state")
    clone, clone_real = linked(tmp_path, "clone")
    worktree, worktree_real = linked(tmp_path, "worktree")
    protected = launch.protected(config, state)
    policy = Policy(repository_label="widgets", sandbox_domains=["github.com", "proxy.golang.org"])
    local = LocalConfig(sandbox_write=["~/.cache/uv", "/opt/cache"])
    settings = launch.hand_settings("ACME-7", clone, worktree, policy, local, protected)
    return {
        "settings": settings,
        "captain": launch.captain_settings(protected),
        "secrets": [config, config_real, home / ".config" / "deckhand", home / ".config" / "gh"],
        "state": (state, state_real),
        "clone": (clone, clone_real),
        "worktree": (worktree, worktree_real),
    }


def covers(pattern: str, target: str) -> bool:
    if pattern.endswith("/**"):
        root = pattern[:-3]
        return target == root or target.startswith(root + "/")
    return target == pattern or target.startswith(pattern + "/")


def test_every_protected_path_denied_to_reads_writes_and_tools(layout: dict) -> None:
    for settings in (layout["settings"], layout["captain"]):
        deny = settings["permissions"]["deny"]
        filesystem = settings["sandbox"]["filesystem"]
        files = settings["sandbox"]["credentials"]["files"]
        for path in layout["secrets"]:
            assert rule("Read", path, "/**") in deny
            assert rule("Edit", path, "/**") in deny
            assert str(path) in filesystem["denyWrite"]
            assert str(path) in filesystem["denyRead"]
            assert {"path": str(path), "mode": "deny"} in files
        envs = settings["sandbox"]["credentials"]["envVars"]
        assert envs == [{"name": name, "mode": "deny"} for name in ("GH_TOKEN", "GITHUB_TOKEN", "LINEAR_API_KEY")]


def test_state_dir_readable_but_never_writable(layout: dict) -> None:
    for settings in (layout["settings"], layout["captain"]):
        deny = settings["permissions"]["deny"]
        filesystem = settings["sandbox"]["filesystem"]
        files = [entry["path"] for entry in settings["sandbox"]["credentials"]["files"]]
        for state in layout["state"]:
            target = str(state / "review" / "target.md")
            assert not [p for p in reads_denied(settings) if covers(p, target)]
            assert rule("Edit", state, "/**") in deny
            assert str(state) in filesystem["denyWrite"]
            assert str(state) not in filesystem["denyRead"] and str(state) not in files


def reads_denied(settings: dict) -> list[str]:
    rules = [entry[6:-1] for entry in settings["permissions"]["deny"] if entry.startswith("Read(")]
    files = [entry["path"] for entry in settings["sandbox"]["credentials"]["files"]]
    return rules + settings["sandbox"]["filesystem"]["denyRead"] + files


@pytest.mark.parametrize("name", ["con*fig", "con?fig", "con[f]ig"])
def test_a_path_with_wildcard_characters_refuses(tmp_path: Path, home: Path, name: str) -> None:
    with pytest.raises(Refusal, match="wildcard"):
        launch.protected(tmp_path / name, tmp_path / "state")
    protected = launch.protected(tmp_path / "config", tmp_path / "state")
    with pytest.raises(Refusal, match="wildcard"):
        launch.hand_settings(
            "ACME-7", tmp_path / name, tmp_path, Policy(repository_label="w"), LocalConfig(), protected
        )


def test_no_exempt_pattern_runs_git(layout: dict) -> None:
    sandbox = layout["settings"]["sandbox"]
    assert sandbox["excludedCommands"] == ["deckhand *", "gh pr view *", "gh pr diff *", "gh pr checks *"]
    assert layout["captain"]["sandbox"]["excludedCommands"] == ["deckhand *"]
    for settings in (layout["settings"], layout["captain"]):
        assert not any(c.split()[0] == "git" for c in settings["sandbox"]["excludedCommands"])
        assert settings["sandbox"]["enabled"] is True
        assert settings["sandbox"]["allowUnsandboxedCommands"] is False
        assert settings["sandbox"]["autoAllowBashIfSandboxed"] is True
    assert layout["settings"]["permissions"]["allow"] == ["Bash(deckhand *)"]


def test_hand_cannot_ask_or_message(layout: dict) -> None:
    settings = layout["settings"]
    deny = settings["permissions"]["deny"]
    assert {"AskUserQuestion", "SendMessage", "ListAgents"} <= set(deny)
    assert "SendMessage" not in settings["permissions"]["allow"]
    assert settings["env"]["DECKHAND_ROLE"] == "hand"
    assert settings["env"]["DECKHAND_STORY"] == "ACME-7"
    assert settings["sandbox"]["network"]["allowedDomains"] == ["github.com", "proxy.golang.org"]
    assert settings["sandbox"]["filesystem"]["allowWrite"] == ["~/.cache/uv", "/opt/cache"]


def test_git_metadata_unwritable(layout: dict) -> None:
    deny = layout["settings"]["permissions"]["deny"]
    deny_write = layout["settings"]["sandbox"]["filesystem"]["denyWrite"]
    for clone in layout["clone"]:
        assert rule("Edit", clone / ".git", "/**") in deny
        assert str(clone / ".git") in deny_write
    for worktree in layout["worktree"]:
        assert rule("Edit", worktree / ".git") in deny
        assert str(worktree / ".git") in deny_write


def test_claude_config_denied_to_file_tools(layout: dict) -> None:
    deny = layout["settings"]["permissions"]["deny"]
    for worktree in layout["worktree"]:
        for name in CLAUDE_CONFIG:
            assert f"Edit(//{str(worktree).lstrip('/')}/{name})" in deny
    assert not any(".claude" in path for path in layout["settings"]["sandbox"]["filesystem"]["denyWrite"])


def test_deckhand_policy_and_lenses_stay_editable(layout: dict) -> None:
    settings = layout["settings"]
    patterns = [
        entry.split("(", 1)[1][:-1][1:]
        for entry in settings["permissions"]["deny"]
        if entry.startswith(("Edit(", "Read("))
    ]
    patterns += settings["sandbox"]["filesystem"]["denyWrite"]
    for worktree in layout["worktree"]:
        for target in (worktree / ".claude" / "deckhand.toml", worktree / ".claude" / "deckhand" / "lenses" / "x.md"):
            assert not [p for p in patterns if covers(p, str(target))], target


def test_captain_keeps_dialog_and_messaging(layout: dict) -> None:
    captain = layout["captain"]
    deny = captain["permissions"]["deny"]
    assert not {"AskUserQuestion", "SendMessage", "ListAgents"} & set(deny)
    assert captain["permissions"]["allow"] == ["Bash(deckhand *)", "SendMessage"]
    assert captain["env"] == {"DECKHAND_ROLE": "captain"}


PLUGIN_BIN = str(Path(launch.__file__).resolve().parent.parent / "bin")


def test_hand_env_carries_dirs_and_plugin_bin_first(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protected = launch.protected(tmp_path / "config", tmp_path / "state")
    policy = Policy(repository_label="widgets")
    monkeypatch.setenv("DECKHAND_CONFIG_DIR", "/c")
    monkeypatch.setenv("DECKHAND_STATE_DIR", "/s")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = launch.hand_settings("ACME-7", tmp_path, tmp_path, policy, LocalConfig(), protected)["env"]
    assert env == {
        "DECKHAND_ROLE": "hand",
        "DECKHAND_HAND_KIND": "story",
        "DECKHAND_STORY": "ACME-7",
        "DECKHAND_CONFIG_DIR": "/c",
        "DECKHAND_STATE_DIR": "/s",
        "PATH": f"{PLUGIN_BIN}:/usr/bin",
    }
    monkeypatch.setenv("DECKHAND_PLUGIN_DIR", "/plugin")
    env = launch.hand_settings("ACME-7", tmp_path, tmp_path, policy, LocalConfig(), protected)["env"]
    assert env["PATH"] == "/plugin/bin:/usr/bin"
    monkeypatch.delenv("PATH")
    env = launch.hand_settings("ACME-7", tmp_path, tmp_path, policy, LocalConfig(), protected)["env"]
    assert env["PATH"] == "/plugin/bin"


@pytest.mark.parametrize(("kind", "variable"), [("draft", "DECKHAND_STORY"), ("spike", "DECKHAND_SPIKE")])
def test_hand_env_names_the_kind_of_hand(tmp_path: Path, home: Path, kind: str, variable: str) -> None:
    protected = launch.protected(tmp_path / "config", tmp_path / "state")
    policy = Policy(repository_label="widgets")
    env = launch.hand_settings("ACME-7", tmp_path, tmp_path, policy, LocalConfig(), protected, kind=kind)["env"]
    assert (env["DECKHAND_HAND_KIND"], env[variable]) == (kind, "ACME-7")
    assert {"DECKHAND_STORY", "DECKHAND_SPIKE"} & env.keys() == {variable}


def test_hand_path_holds_only_trusted_entries(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp, clone, worktree, state = (tmp_path / name for name in ("temp", "clone", "worktree", "state"))
    for directory in (temp, clone, worktree, state):
        directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(state))
    monkeypatch.setenv("DECKHAND_PLUGIN_DIR", "/plugin")
    dirty = [".", f"{temp}/x", "", "rel", f"{clone}/bin", f"{worktree}/node/bin", f"{state}/bin", "/usr/bin", "/bin"]
    monkeypatch.setenv("PATH", os.pathsep.join(dirty))
    protected = launch.protected(tmp_path / "config", state)
    policy = Policy(repository_label="widgets")
    env = launch.hand_settings("ACME-7", clone, worktree, policy, LocalConfig(), protected)["env"]
    assert env["PATH"].split(os.pathsep) == ["/plugin/bin", "/usr/bin", "/bin"]


def test_resume_argv_carries_settings() -> None:
    settings = {"env": {"DECKHAND_ROLE": "hand"}, "sandbox": {"enabled": True}}
    argv = launch.hand_argv("hand-ACME-7", settings, "A decision of the person's is recorded.", None, "sess-1")
    assert argv[:8] == [
        "claude",
        "--bg",
        "--name",
        "hand-ACME-7",
        "--permission-mode",
        "auto",
        "--settings",
        argv[7],
    ]
    assert json.loads(argv[7]) == settings
    assert argv[8:] == ["--resume", "sess-1", "A decision of the person's is recorded."]
    assert "--plugin-dir" not in argv


def test_start_argv_names_the_prompt_and_plugin_dir_for_testing() -> None:
    argv = launch.hand_argv("hand-ACME-7", {}, "/deckhand:story ACME-7", Path("/plugin"), None)
    assert argv == [
        "claude",
        "--bg",
        "--name",
        "hand-ACME-7",
        "--permission-mode",
        "auto",
        "--settings",
        "{}",
        "--plugin-dir",
        "/plugin",
        "/deckhand:story ACME-7",
    ]


GIT_CREDENTIALS = (".ssh", ".git-credentials", ".netrc", ".config/git/credentials", ".gnupg")


def test_file_tools_write_only_in_working_directories(
    tmp_path: Path, layout: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp, temp_real = linked(tmp_path, "temp")
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    protected = launch.protected(tmp_path / "config", tmp_path / "state")
    policy = Policy(repository_label="widgets")
    permissions = launch.hand_settings("ACME-7", tmp_path, tmp_path, policy, LocalConfig(), protected)["permissions"]
    assert permissions["blockWritesOutsideWorkingDirectories"] is True
    assert permissions["additionalDirectories"] == [str(temp), str(temp_real)]
    assert "blockWritesOutsideWorkingDirectories" not in layout["captain"]["permissions"]


def test_hand_has_no_mcp_tools(layout: dict) -> None:
    assert "mcp__*" in layout["settings"]["permissions"]["deny"]
    assert layout["settings"]["disableClaudeAiConnectors"] is True
    assert "mcp__*" not in layout["captain"]["permissions"]["deny"]
    assert "disableClaudeAiConnectors" not in layout["captain"]


def test_git_credentials_unreadable(tmp_path: Path, home: Path) -> None:
    real_ssh = (tmp_path / "keys").resolve()
    real_ssh.mkdir()
    (home / ".ssh").symlink_to(real_ssh)
    protected = launch.protected(tmp_path / "config", tmp_path / "state")
    policy = Policy(repository_label="widgets")
    settings = launch.hand_settings("ACME-7", tmp_path, tmp_path, policy, LocalConfig(), protected)
    deny = settings["permissions"]["deny"]
    deny_read = settings["sandbox"]["filesystem"]["denyRead"]
    for path in [*(home / name for name in GIT_CREDENTIALS), real_ssh]:
        assert rule("Read", path) in deny
        assert rule("Read", path, "/**") in deny
        assert str(path) in deny_read


@pytest.mark.parametrize("entry", ["tools", "tools/bin"])
def test_sandbox_write_holding_a_path_dir_refuses(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    bin_dir = tmp_path / "tools" / "bin"
    bin_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere-temp"
    elsewhere.mkdir()
    # The test folder may sit under /tmp, which deckhand never trusts on PATH.
    monkeypatch.setattr(git, "_untrusted_roots", lambda extra=(): [elsewhere.resolve()])
    monkeypatch.setenv("PATH", f"/usr/bin{os.pathsep}{bin_dir}")
    protected = launch.protected(tmp_path / "config", tmp_path / "state")
    policy = Policy(repository_label="widgets")
    local = LocalConfig(sandbox_write=[str(tmp_path / entry)])
    with pytest.raises(Refusal, match="PATH"):
        launch.hand_settings("ACME-7", tmp_path / "clone", tmp_path / "clone", policy, local, protected)
    local = LocalConfig(sandbox_write=[str(tmp_path / "cache")])
    launch.hand_settings("ACME-7", tmp_path / "clone", tmp_path / "clone", policy, local, protected)


def test_the_captain_opens_in_focus_view_and_a_hand_does_not(layout: dict) -> None:
    assert layout["captain"]["viewMode"] == "focus"
    assert "viewMode" not in layout["settings"]


LAUNCH_OUTPUT = (
    "Starting background service…\nbackgrounded · 1444880d · probe-c\n"
    "  claude agents             list sessions\n  claude attach 1444880d    open in this terminal\n"
    "  claude logs 1444880d      show recent output\n  claude stop 1444880d      stop this session\n"
)
COPY_OUTPUT = (
    "note: session 1444880d is already running in the background, so this started a copy as 4a65b4bd."
    " `claude attach 1444880d` opens the original.\nbackgrounded · 4a65b4bd\n"
    "  claude agents             list sessions\n  claude attach 4a65b4bd    open in this terminal\n"
)


def test_backgrounded_reads_the_short_id_and_a_copy() -> None:
    assert launch.backgrounded(LAUNCH_OUTPUT) == ("1444880d", None)
    assert launch.backgrounded(COPY_OUTPUT) == ("4a65b4bd", "4a65b4bd")
    assert launch.backgrounded("") == (None, None)


def test_a_resume_names_the_session_and_the_text_alone() -> None:
    assert launch.resume_argv("sid-1", "Mail.") == ["claude", "--bg", "--resume", "sid-1", "Mail."]
