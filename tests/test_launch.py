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
    assert layout["settings"]["sandbox"]["autoAllowBashIfSandboxed"] is True
    assert layout["settings"]["permissions"]["allow"] == ["Bash(deckhand *)"]


def test_a_hand_refuses_peer_messages_and_cannot_notify(layout: dict) -> None:
    settings = layout["settings"]
    deny = settings["permissions"]["deny"]
    assert {"AskUserQuestion", "SendMessage", "ListAgents", "PushNotification"} <= set(deny)
    assert settings["crossSessionInbound"] == "refuse"
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


def test_the_captain_can_ask_read_and_plan_but_not_send_or_notify(layout: dict) -> None:
    permissions = layout["captain"]["permissions"]
    tools = [rule for rule in permissions["allow"] if not rule.startswith(("Write(", "Edit("))]
    assert tools == [
        "Bash(deckhand *)",
        "Read",
        "Grep",
        "Glob",
        "Agent",
        "AskUserQuestion",
        "Skill(deckhand:feature)",
        "Skill(deckhand:signoff)",
        "Skill(superpowers:*)",
        "Skill(impeccable:*)",
        *(f"Bash({command} *)" for command in launch.READ_ONLY),
    ]
    unprompted = set(launch.READ_ONLY) | set(permissions["allow"])
    assert not {"find", "git diff", "git log", "git show", "Bash", "Write", "Edit"} & unprompted
    assert permissions["defaultMode"] == "default"
    assert {"SendMessage", "PushNotification", "NotebookEdit", "mcp__*"} <= set(permissions["deny"])
    assert "AskUserQuestion" not in permissions["deny"]
    assert layout["captain"]["sandbox"]["autoAllowBashIfSandboxed"] is False
    assert layout["captain"]["env"] == {"DECKHAND_ROLE": "captain"}


def test_the_captain_writes_only_in_the_deckhand_input_folders(layout: dict) -> None:
    permissions = layout["captain"]["permissions"]
    roots = launch.captain_inputs()
    assert {root.name for root in roots} == {launch.INPUT}
    assert {root.parent for root in roots} == set(launch.input_roots())
    writes = [rule for rule in permissions["allow"] if rule.startswith(("Write(", "Edit("))]
    assert writes == [rule(tool, root, "/**") for root in roots for tool in ("Write", "Edit")]
    assert not {"Write", "Edit"} & set(permissions["allow"])
    assert permissions["additionalDirectories"] == [str(root) for root in roots]
    assert str(Path(tempfile.gettempdir()).resolve() / launch.INPUT) in permissions["additionalDirectories"]
    assert f"/tmp/claude-{os.getuid()}/{launch.INPUT}" in permissions["additionalDirectories"]
    for path in layout["secrets"]:
        assert rule("Edit", path, "/**") in permissions["deny"]


def test_the_captain_rings_the_terminal_bell_and_refuses_peer_messages(layout: dict) -> None:
    captain = layout["captain"]
    assert (captain["preferredNotifChannel"], captain["crossSessionInbound"]) == ("terminal_bell", "refuse")


def test_a_hand_runs_uv_without_a_cache(layout: dict) -> None:
    assert layout["settings"]["env"]["UV_NO_CACHE"] == "1"


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
        "UV_NO_CACHE": "1",
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


def test_hand_argv_carries_settings_and_ends_in_the_prompt() -> None:
    settings = {"env": {"DECKHAND_ROLE": "hand"}, "sandbox": {"enabled": True}}
    argv = launch.hand_argv("hand-ACME-7", settings, "A decision of the person's is recorded.", None)
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
    assert argv[8:] == ["A decision of the person's is recorded."]
    assert "--plugin-dir" not in argv


def test_start_argv_names_the_prompt_and_plugin_dir_for_testing() -> None:
    argv = launch.hand_argv("hand-ACME-7", {}, "/deckhand:story ACME-7", Path("/plugin"))
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


CREDENTIALS = (".ssh", ".git-credentials", ".netrc", ".config/git/credentials", ".gnupg", ".aws", ".config/gcloud")


def test_file_tools_write_only_in_working_directories(
    tmp_path: Path, layout: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp, temp_real = linked(tmp_path, "temp")
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    protected = launch.protected(tmp_path / "config", tmp_path / "state")
    policy = Policy(repository_label="widgets")
    permissions = launch.hand_settings("ACME-7", tmp_path, tmp_path, policy, LocalConfig(), protected)["permissions"]
    assert permissions["blockWritesOutsideWorkingDirectories"] is True
    uid = os.getuid()
    sandbox_tmp = [f"/tmp/claude-{uid}", str(Path(f"/tmp/claude-{uid}").resolve())]
    assert permissions["additionalDirectories"] == [str(temp), str(temp_real), *dict.fromkeys(sandbox_tmp)]
    assert "blockWritesOutsideWorkingDirectories" not in layout["captain"]["permissions"]


def test_hand_has_no_mcp_tools(layout: dict) -> None:
    assert "mcp__*" in layout["settings"]["permissions"]["deny"]
    assert layout["settings"]["disableClaudeAiConnectors"] is True
    assert "disableClaudeAiConnectors" not in layout["captain"]


def both_roles(tmp_path: Path) -> list[dict]:
    protected = launch.protected(tmp_path / "config", tmp_path / "state")
    policy = Policy(repository_label="widgets")
    hand = launch.hand_settings("ACME-7", tmp_path, tmp_path, policy, LocalConfig(), protected)
    return [hand, launch.captain_settings(protected)]


def test_credentials_unreadable_to_both_roles(tmp_path: Path, home: Path) -> None:
    real_ssh = (tmp_path / "keys").resolve()
    real_ssh.mkdir()
    (home / ".ssh").symlink_to(real_ssh)
    for settings in both_roles(tmp_path):
        deny = settings["permissions"]["deny"]
        deny_read = settings["sandbox"]["filesystem"]["denyRead"]
        for path in [*(home / name for name in CREDENTIALS), real_ssh]:
            assert rule("Read", path) in deny
            assert rule("Read", path, "/**") in deny
            assert str(path) in deny_read


def test_the_captain_cannot_read_a_key_or_cat_netrc(tmp_path: Path, home: Path) -> None:
    captain = both_roles(tmp_path)[1]
    reads = [r[len("Read(/") : -1] for r in captain["permissions"]["deny"] if r.startswith("Read(")]
    assert any(covers(pattern, str(home / ".ssh" / "id_ed25519")) for pattern in reads)
    sandbox = captain["sandbox"]
    assert sandbox["enabled"] is True and sandbox["allowUnsandboxedCommands"] is False
    assert not [command for command in sandbox["excludedCommands"] if command.startswith("cat")]
    assert any(covers(pattern, str(home / ".netrc")) for pattern in captain["sandbox"]["filesystem"]["denyRead"])


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
    "Starting background service\u2026\nbackgrounded \u00b7 1444880d \u00b7 probe-c\n"
    "  claude agents             list sessions\n  claude attach 1444880d    open in this terminal\n"
    "  claude logs 1444880d      show recent output\n  claude stop 1444880d      stop this session\n"
)
COPY_OUTPUT = (
    "note: session 1444880d is already running in the background, so this started a copy as 4a65b4bd."
    " `claude attach 1444880d` opens the original.\nbackgrounded \u00b7 4a65b4bd\n"
    "  claude agents             list sessions\n  claude attach 4a65b4bd    open in this terminal\n"
)


def test_backgrounded_reads_the_short_id_and_a_copy() -> None:
    assert launch.backgrounded(LAUNCH_OUTPUT) == ("1444880d", None)
    assert launch.backgrounded(COPY_OUTPUT) == ("4a65b4bd", "4a65b4bd")
    assert launch.backgrounded("") == (None, None)


def test_a_resume_names_the_session_and_the_text_alone() -> None:
    assert launch.resume_argv("sid-1", "Mail.") == ["claude", "--bg", "--resume", "sid-1", "Mail."]


def test_the_input_folder_is_made_private_under_the_temporary_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    folder = launch.input_folder()
    assert folder == tmp_path / launch.INPUT
    assert folder.is_dir() and (folder.stat().st_mode & 0o777) == 0o700
    assert launch.input_folder() == folder
