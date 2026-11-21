from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import tomllib
from datetime import datetime
from pathlib import Path

import pytest

from deckhand import statusline
from deckhand.statusline import Places
from deckhand.step import Refusal

NOW = datetime(2026, 11, 20, 9, 30, 0)
INPUT = '{"model": {"display_name": "Opus"}, "cwd": "/w"}\n\n'
PLAIN = "captain: 2 waiting"
COLORED = "\x1b[33mcaptain: 2 waiting\x1b[0m"
FAKE_DECKHAND = """#!/bin/sh
cat > /dev/null
case " $* " in *" captain context --line "*) ;; *) exit 9 ;; esac
case " $* " in *" --color "*) printf '\\033[33mcaptain: 2 waiting\\033[0m\\n' ;; *) echo 'captain: 2 waiting' ;; esac
"""
HUD_POSIX = "'/opt/node' '/x/.claude/plugins/claude-hud/statusline.mjs'"
HUD_BASH_C = (
    'bash -c \'plugin_dir=$(ls -d "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/plugins/cache/*/claude-hud/*/ 2>/dev/null'
    " | awk -F/ '\"'\"'{ print $(NF-1) \"\\t\" $(0) }'\"'\"' | tail -1 | cut -f2-);"
    ' exec "NODE" "${plugin_dir}dist/index.js"\''
)
SETTINGS = '{\n    "model": "opus",\n    "statusLine": {"type": "command", "command": COMMAND, "padding": 1}\n}'


def fake_root(base: Path) -> Path:
    root = base / "plugin root"
    (root / "bin").mkdir(parents=True)
    deckhand = root / "bin" / "deckhand"
    deckhand.write_text(FAKE_DECKHAND)
    deckhand.chmod(0o755)
    return root


@pytest.fixture
def places(tmp_path: Path) -> Places:
    home = tmp_path / "home"
    return Places(
        settings=home / ".claude" / "settings.json",
        managed=tmp_path / "managed",
        project=tmp_path / "project",
        ccstatusline=home / ".config" / "ccstatusline" / "settings.json",
        ccline=home / ".claude" / "ccline" / "config.toml",
        config=tmp_path / "deck hand's $cfg",
        root=fake_root(tmp_path),
    )


def with_command(places: Places, command: str) -> bytes:
    raw = SETTINGS.replace("COMMAND", json.dumps(command)).encode()
    places.settings.parent.mkdir(parents=True, exist_ok=True)
    places.settings.write_bytes(raw)
    return raw


def settings_line(places: Places) -> dict:
    return json.loads(places.settings.read_text())["statusLine"]


def run(command: str, stdin: str = INPUT, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/bin/sh", "-c", command], input=stdin, capture_output=True, text=True, env=env, check=False)


def snapshot(base: Path) -> dict[str, bytes]:
    return {str(path.relative_to(base)): path.read_bytes() for path in sorted(base.rglob("*")) if path.is_file()}


def segment(places: Places) -> str:
    return "DECKHAND_STATUSLINE=1 " + shlex.quote(str(places.config / "statusline-segment"))


def wrapper(places: Places) -> str:
    return "DECKHAND_STATUSLINE=1 " + shlex.quote(str(places.config / "statusline-wrapper"))


def fake_node(tmp_path: Path) -> Path:
    node = tmp_path / "fake node"
    node.write_text('#!/bin/sh\necho "allow=$CLAUDE_HUD_ALLOW_EXTRA_CMD"\nfor arg in "$@"; do echo "arg=$arg"; done\n')
    node.chmod(0o755)
    return node


ccstatusline_settings = {
    "version": 3,
    "lines": [[{"id": "1", "type": "model", "color": "cyan"}], [], []],
    "colorLevel": 2,
}
CCLINE = 'version = 2\nskin = "plain"\n\n[[line]]\nleft = ["model", "context"]\n'


def owner_setup(places: Places, owner: str) -> None:
    if owner == "none":
        places.settings.parent.mkdir(parents=True)
        places.settings.write_text('{\n  "model": "opus"\n}')
    elif owner == "claude-hud":
        with_command(places, HUD_POSIX)
    elif owner == "ccstatusline":
        with_command(places, "npx -y ccstatusline@latest")
        places.ccstatusline.parent.mkdir(parents=True)
        places.ccstatusline.write_text(json.dumps(ccstatusline_settings, indent=4))
    elif owner == "ccometixline":
        with_command(places, "~/.claude/ccline/ccline")
        places.ccline.parent.mkdir(parents=True, exist_ok=True)
        places.ccline.write_text(CCLINE)
    else:
        with_command(places, "printf 'my line'")


OWNERS = ["none", "claude-hud", "ccstatusline", "ccometixline", "other"]


def test_none_gets_deckhands_own_status_line(places: Places, tmp_path: Path):
    owner_setup(places, "none")
    before = places.settings.read_bytes()
    statusline.install(places, NOW)
    data = json.loads(places.settings.read_text())
    assert data == {
        "model": "opus",
        "statusLine": {"type": "command", "command": f"{segment(places)} --color", "refreshInterval": 5},
    }
    backups = list(places.settings.parent.glob("settings.json.deckhand-*"))
    assert [path.read_bytes() for path in backups] == [before]
    assert run(data["statusLine"]["command"]).stdout == COLORED + "\n"


def test_absent_settings_file_is_created_and_removed_again(places: Places):
    statusline.install(places, NOW)
    assert settings_line(places)["command"] == f"{segment(places)} --color"
    statusline.remove(places, NOW)
    assert not places.settings.exists()
    assert [path.name for path in places.config.iterdir()] == [statusline.LAUNCHER]


def test_claude_hud_posix_command_gets_extra_cmd_and_opt_in(places: Places, tmp_path: Path):
    node = fake_node(tmp_path)
    with_command(places, f"{shlex.quote(str(node))} '/x/.claude/plugins/claude-hud/statusline.mjs'")
    statusline.install(places, NOW)
    line = settings_line(places)
    assert line["padding"] == 1
    assert line["command"].startswith("CLAUDE_HUD_ALLOW_EXTRA_CMD=1 ")
    out = run(line["command"]).stdout.splitlines()
    assert out[:3] == ["allow=1", f"arg={HUD_POSIX.split()[1].strip(chr(39))}", "arg=--extra-cmd"]
    extra = out[3].removeprefix("arg=")
    assert extra == segment(places)
    assert run(extra).stdout == PLAIN + "\n"


def test_claude_hud_bash_c_command_gets_extra_cmd_inside_the_script(places: Places, tmp_path: Path):
    node = fake_node(tmp_path)
    with_command(places, HUD_BASH_C.replace("NODE", str(node)))
    statusline.install(places, NOW)
    command = settings_line(places)["command"]
    out = run(command).stdout.splitlines()
    assert out[0] == "allow=1"
    assert out[1].endswith("dist/index.js")
    assert out[2] == "arg=--extra-cmd"
    assert run(out[3].removeprefix("arg=")).stdout == PLAIN + "\n"


def test_claude_hud_command_of_another_shape_is_wrapped(places: Places):
    with_command(places, "cd /tmp && node /x/claude-hud/dist/index.js")
    report = statusline.report(places)
    assert report.owner == "claude-hud"
    assert report.method == "wrapper"
    statusline.install(places, NOW)
    assert settings_line(places)["command"] == wrapper(places)


def test_ccstatusline_gets_a_custom_command_widget_in_its_own_config(places: Places):
    owner_setup(places, "ccstatusline")
    settings_before = places.settings.read_bytes()
    statusline.install(places, NOW)
    assert places.settings.read_bytes() == settings_before
    data = json.loads(places.ccstatusline.read_text())
    assert data["lines"] == [
        [{"id": "1", "type": "model", "color": "cyan"}],
        [{"id": "deckhand-captain", "type": "custom-command", "commandPath": segment(places), "timeout": 2000}],
        [],
    ]
    assert {key: data[key] for key in ("version", "colorLevel")} == {"version": 3, "colorLevel": 2}
    assert run(data["lines"][1][0]["commandPath"]).stdout == PLAIN + "\n"


def test_ccstatusline_config_flag_names_its_file(places: Places, tmp_path: Path):
    custom = tmp_path / "elsewhere" / "cc.json"
    custom.parent.mkdir()
    custom.write_text(json.dumps({"lines": [[{"id": "1", "type": "model"}]]}))
    with_command(places, f"npx -y ccstatusline@latest --config {shlex.quote(str(custom))}")
    statusline.install(places, NOW)
    lines = json.loads(custom.read_text())["lines"]
    assert [widget["id"] for widget in lines[1]] == ["deckhand-captain"]
    assert not places.ccstatusline.exists()


def test_ccstatusline_with_full_lines_appends_after_a_separator(places: Places):
    owner_setup(places, "ccstatusline")
    full = {"lines": [[{"id": "1", "type": "model"}], [{"id": "2", "type": "git-branch"}], [{"id": "3", "type": "x"}]]}
    places.ccstatusline.write_text(json.dumps(full))
    statusline.install(places, NOW)
    last = json.loads(places.ccstatusline.read_text())["lines"][2]
    assert [widget["type"] for widget in last] == ["x", "separator", "custom-command"]


def test_ccstatusline_without_its_settings_file_is_wrapped(places: Places):
    with_command(places, "npx -y ccstatusline@latest")
    report = statusline.report(places)
    assert (report.owner, report.method) == ("ccstatusline", "wrapper")


def test_ccometixline_gets_a_cmd_line(places: Places):
    owner_setup(places, "ccometixline")
    settings_before = places.settings.read_bytes()
    statusline.install(places, NOW)
    assert places.settings.read_bytes() == settings_before
    text = places.ccline.read_text()
    assert text.startswith(CCLINE)
    lines = tomllib.loads(text)["line"]
    assert lines[0] == {"left": ["model", "context"]}
    assert lines[1] == {"left": [{"metric": "cmd", "options": {"run": segment(places), "cache_s": 5}}]}
    assert run(lines[1]["left"][0]["options"]["run"]).stdout == PLAIN + "\n"


@pytest.mark.parametrize("text", [None, 'skin = "plain"\n', "line = []\n", "not toml ["])
def test_ccometixline_without_lines_of_its_own_is_wrapped(places: Places, text: str | None):
    with_command(places, "ccline")
    if text is not None:
        places.ccline.parent.mkdir(parents=True, exist_ok=True)
        places.ccline.write_text(text)
    report = statusline.report(places)
    assert (report.owner, report.method) == ("ccometixline", "wrapper")


def test_other_command_is_wrapped_and_kept_in_deckhands_config(places: Places, tmp_path: Path):
    seen = tmp_path / "seen.json"
    original = f"cat > {shlex.quote(str(seen))}; printf 'my line'"
    with_command(places, original)
    statusline.install(places, NOW)
    line = settings_line(places)
    assert line == {"type": "command", "command": wrapper(places), "padding": 1}
    state = json.loads((places.config / "statusline.json").read_text())
    assert state["original"]["command"] == original
    result = run(line["command"])
    assert result.stdout == f"my line\n{COLORED}\n"
    assert result.returncode == 0
    assert seen.read_text() == INPUT


def test_wrapper_keeps_the_originals_output_and_exit_when_the_segment_fails(places: Places):
    with_command(places, "printf 'first\\nsecond\\n'; exit 3")
    statusline.install(places, NOW)
    (places.root / "bin" / "deckhand").write_text("#!/bin/sh\necho broken >&2\nexit 1\n")
    result = run(settings_line(places)["command"])
    assert (result.stdout, result.stderr, result.returncode) == ("first\nsecond\n", "", 3)


@pytest.mark.parametrize("owner", OWNERS)
def test_second_install_changes_nothing(places: Places, tmp_path: Path, owner: str):
    owner_setup(places, owner)
    statusline.install(places, NOW)
    first = snapshot(tmp_path)
    assert statusline.report(places).installed
    statusline.install(places, datetime(2026, 11, 21))
    assert snapshot(tmp_path) == first


@pytest.mark.parametrize("owner", OWNERS)
def test_remove_restores_every_file_byte_for_byte(places: Places, tmp_path: Path, owner: str):
    owner_setup(places, owner)
    files = (places.settings, places.ccstatusline, places.ccline)
    before = {path: path.read_bytes() for path in files if path.exists()}
    statusline.install(places, NOW)
    statusline.remove(places, NOW)
    assert {path: path.read_bytes() for path in files if path.exists()} == before
    assert not any(places.config.glob("statusline*"))


def test_remove_keeps_what_the_person_changed_since(places: Places):
    original = with_command(places, "printf 'my line'")
    statusline.install(places, NOW)
    data = json.loads(places.settings.read_text())
    data["theme"] = "dark"
    places.settings.write_text(json.dumps(data, indent=2))
    statusline.remove(places, NOW)
    restored = json.loads(places.settings.read_text())
    assert restored == {**json.loads(original), "theme": "dark"}


def test_remove_refuses_a_changed_line_that_still_runs_the_segment(places: Places, tmp_path: Path):
    with_command(places, "printf 'my line'")
    statusline.install(places, NOW)
    data = json.loads(places.settings.read_text())
    data["statusLine"]["command"] += " && true"
    places.settings.write_text(json.dumps(data))
    before = snapshot(tmp_path)
    with pytest.raises(Refusal, match="changed since setup"):
        statusline.remove(places, NOW)
    assert snapshot(tmp_path) == before


def test_remove_without_an_install_writes_nothing(places: Places, tmp_path: Path):
    owner_setup(places, "other")
    before = snapshot(tmp_path)
    assert statusline.remove(places, NOW) == ["status line: nothing to remove"]
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("file", "content", "rule"),
    [
        ("managed-settings.json", {"statusLine": {"type": "command", "command": "x"}}, "set the status line"),
        ("managed-settings.d/10-team.json", {"statusLine": {"type": "command", "command": "x"}}, "set the status line"),
        ("managed-settings.json", {"allowManagedHooksOnly": True}, "only a managed status line"),
        ("managed-settings.json", {"disableAllHooks": True}, "turns the status line off"),
    ],
)
def test_managed_settings_refuse_before_any_write(places: Places, tmp_path: Path, file: str, content: dict, rule: str):
    owner_setup(places, "other")
    path = places.managed / file
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(content))
    before = snapshot(tmp_path)
    with pytest.raises(Refusal, match=rule):
        statusline.install(places, NOW)
    assert snapshot(tmp_path) == before
    assert rule in statusline.report(places).blocked


def test_user_disable_all_hooks_refuses(places: Places, tmp_path: Path):
    places.settings.parent.mkdir(parents=True)
    places.settings.write_text('{"disableAllHooks": true}')
    before = snapshot(tmp_path)
    with pytest.raises(Refusal, match="turns the status line off"):
        statusline.install(places, NOW)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_unreadable_settings_refuse_and_report(places: Places, tmp_path: Path, text: str):
    places.settings.parent.mkdir(parents=True)
    places.settings.write_text(text)
    before = snapshot(tmp_path)
    with pytest.raises(Refusal, match="settings.json"):
        statusline.install(places, NOW)
    assert snapshot(tmp_path) == before
    report = statusline.report(places)
    assert report.owner == "unknown" and "settings.json" in report.blocked


@pytest.mark.parametrize(
    ("owner", "method", "changed"),
    [
        ("none", "statusLine", ["settings"]),
        ("claude-hud", "extra-cmd", ["settings"]),
        ("ccstatusline", "widget", ["ccstatusline"]),
        ("ccometixline", "cmd item", ["ccline"]),
        ("other", "wrapper", ["settings"]),
    ],
)
def test_report_names_the_owner_and_what_would_change(
    places: Places, tmp_path: Path, owner: str, method: str, changed: list[str]
):
    owner_setup(places, owner)
    before = snapshot(tmp_path)
    report = statusline.report(places)
    assert snapshot(tmp_path) == before
    assert (report.owner, report.method, report.installed) == (owner, method, False)
    assert report.changes == tuple(getattr(places, name) for name in changed)


@pytest.mark.parametrize(("owner", "after"), [("none", "deckhand"), ("claude-hud", "claude-hud"), ("other", "other")])
def test_report_after_install_names_the_owner(places: Places, owner: str, after: str):
    owner_setup(places, owner)
    statusline.install(places, NOW)
    report = statusline.report(places)
    assert (report.owner, report.installed, report.changes) == (after, True, ())


def test_report_notes_a_project_status_line(places: Places):
    owner_setup(places, "none")
    project = places.project / ".claude" / "settings.local.json"
    project.parent.mkdir(parents=True)
    project.write_text('{"statusLine": {"type": "command", "command": "x"}}')
    assert any(str(project) in note for note in statusline.report(places).notes)


def cache_root(base: Path, *versions: str, orphaned: tuple[str, ...] = ()) -> Path:
    plugin = base / "plugins" / "cache" / "acme" / "deckhand"
    for version in versions:
        bin_dir = plugin / version / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "deckhand").write_text(f'#!/bin/sh\necho {version} "$@"\n')
        (bin_dir / "deckhand").chmod(0o755)
        if version in orphaned:
            (plugin / version / ".orphaned_at").write_text("1")
    return plugin / versions[0]


def test_segment_from_the_plugin_cache_runs_the_newest_installed_version(places: Places, tmp_path: Path):
    root = cache_root(tmp_path, "1.2.0", "1.10.0", "2.0.0", orphaned=("2.0.0",))
    places = Places(**{**places.__dict__, "root": root})
    statusline.install(places, NOW)
    assert run(segment(places)).stdout == "1.10.0 captain context --line\n"
    cache_root(tmp_path, "1.11.0")
    assert run(f"{segment(places)} --color").stdout == "1.11.0 captain context --line --color\n"


def test_segment_outside_the_cache_runs_the_root_setup_ran_from(places: Places):
    statusline.install(places, NOW)
    assert run(segment(places)).stdout == PLAIN + "\n"


def test_segment_prints_nothing_when_no_version_is_installed(places: Places, tmp_path: Path):
    root = cache_root(tmp_path, "1.0.0")
    places = Places(**{**places.__dict__, "root": root})
    statusline.install(places, NOW)
    (root / ".orphaned_at").write_text("1")
    result = run(segment(places))
    assert (result.stdout, result.returncode) == ("", 0)


def test_places_follow_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CCLINE_HOME", str(tmp_path / "ccline"))
    places = Places.here()
    assert places.settings == tmp_path / "claude" / "settings.json"
    assert places.ccline == tmp_path / "ccline" / "config.toml"
    assert places.ccstatusline == tmp_path / "home" / ".config" / "ccstatusline" / "settings.json"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.delenv("CCLINE_HOME")
    places = Places.here()
    assert places.settings == tmp_path / "home" / ".claude" / "settings.json"
    assert places.ccline == tmp_path / "home" / ".claude" / "ccline" / "config.toml"


def test_segment_never_waits_on_a_stdin_left_open(places: Places):
    statusline.install(places, NOW)
    read_end, write_end = os.pipe()
    try:
        result = subprocess.run(
            ["/bin/sh", "-c", segment(places)], stdin=read_end, capture_output=True, text=True, timeout=10
        )
    finally:
        os.close(read_end)
        os.close(write_end)
    assert result.stdout == PLAIN + "\n"


def test_deckhand_config_dir_is_created_private(places: Places):
    statusline.install(places, NOW)
    assert places.config.stat().st_mode & 0o777 == 0o700


def test_a_symlinked_settings_file_keeps_its_link_and_mode(places: Places, tmp_path: Path):
    target = tmp_path / "dotfiles" / "settings.json"
    target.parent.mkdir()
    target.write_bytes(b'{"statusLine": {"type": "command", "command": "printf mine"}}\n')
    target.chmod(0o640)
    places.settings.parent.mkdir(parents=True)
    places.settings.symlink_to(target)
    statusline.install(places, NOW)
    assert places.settings.is_symlink()
    assert "statusline-wrapper" in target.read_text()
    assert target.stat().st_mode & 0o777 == 0o640
    statusline.remove(places, NOW)
    assert places.settings.is_symlink()
    assert target.read_bytes() == b'{"statusLine": {"type": "command", "command": "printf mine"}}\n'
    assert target.stat().st_mode & 0o777 == 0o640


def test_remove_refuses_a_ccline_config_its_tool_rewrote(places: Places, tmp_path: Path):
    owner_setup(places, "ccometixline")
    statusline.install(places, NOW)
    run_line = tomllib.loads(places.ccline.read_text())["line"][1]["left"][0]["options"]["run"]
    item = f'{{ metric = "cmd", options = {{ run = {json.dumps(run_line)} }} }}'
    places.ccline.write_text(f'version = 2\n\n[[line]]\nleft = ["model"]\n\n[[line]]\nleft = [{item}]\n')
    before = snapshot(tmp_path)
    with pytest.raises(Refusal, match="changed since setup"):
        statusline.remove(places, NOW)
    assert snapshot(tmp_path) == before


SHELLS = [shell for shell in ("/bin/sh", "/bin/dash", "/bin/bash") if Path(shell).exists()]


@pytest.mark.parametrize("shell", SHELLS)
def test_wrapper_and_launcher_run_under_each_posix_shell(places: Places, tmp_path: Path, shell: str):
    seen = tmp_path / "seen.json"
    with_command(places, f"cat > {shlex.quote(str(seen))}; printf 'my line'; exit 4")
    statusline.install(places, NOW)
    wrapper = subprocess.run(
        [shell, str(places.config / "statusline-wrapper")], input=INPUT, capture_output=True, text=True, check=False
    )
    assert (wrapper.stdout, wrapper.returncode) == (f"my line\n{COLORED}\n", 4)
    assert seen.read_text() == INPUT
    root = cache_root(tmp_path, "1.9.0", "1.10.0")
    statusline.remove(places, NOW)
    statusline.install(Places(**{**places.__dict__, "root": root}), NOW)
    launcher = subprocess.run(
        [shell, str(places.config / "statusline-segment")], capture_output=True, text=True, check=False
    )
    assert launcher.stdout == "1.10.0 captain context --line\n"


def slow_deckhand(places: Places) -> None:
    (places.root / "bin" / "deckhand").write_text("#!/bin/sh\nexec sleep 30\n")


def test_a_hung_segment_is_cut_off_and_never_holds_the_line(places: Places):
    with_command(places, "printf 'my line'")
    statusline.install(places, NOW)
    slow_deckhand(places)
    started = time.monotonic()
    result = run(settings_line(places)["command"])
    assert time.monotonic() - started < 5
    assert (result.stdout, result.returncode) == ("my line\n", 0)
    started = time.monotonic()
    assert run(segment(places)).stdout == ""
    assert time.monotonic() - started < 5


def test_the_wrapper_never_runs_itself(places: Places):
    with_command(places, "printf 'my line'")
    statusline.install(places, NOW)
    nested = run(settings_line(places)["command"], env={**os.environ, "DECKHAND_STATUSLINE_WRAPPED": "1"})
    assert (nested.stdout, nested.returncode) == ("", 0)


def test_a_line_that_names_deckhands_files_unmarked_is_refused(places: Places, tmp_path: Path):
    with_command(places, shlex.quote(str(places.config / "statusline-wrapper")))
    before = snapshot(tmp_path)
    with pytest.raises(Refusal, match="already runs"):
        statusline.install(places, NOW)
    assert snapshot(tmp_path) == before


def test_the_original_is_kept_even_when_it_holds_a_placeholder(places: Places):
    with_command(places, "echo '@SEGMENT@'")
    statusline.install(places, NOW)
    assert run(settings_line(places)["command"]).stdout == f"@SEGMENT@\n{COLORED}\n"


def test_a_half_written_newest_version_is_skipped(places: Places, tmp_path: Path):
    root = cache_root(tmp_path, "1.0.0")
    (root.parent / "1.1.0").mkdir()
    statusline.install(Places(**{**places.__dict__, "root": root}), NOW)
    assert run(segment(places)).stdout == "1.0.0 captain context --line\n"


def test_remove_leaves_a_ccstatusline_file_without_deckhands_widget(places: Places, tmp_path: Path):
    owner_setup(places, "ccstatusline")
    statusline.install(places, NOW)
    places.ccstatusline.write_text('{"lines": [[{"id": "1", "type": "model"}]]}')
    statusline.remove(places, NOW)
    assert places.ccstatusline.read_text() == '{"lines": [[{"id": "1", "type": "model"}]]}'
    assert list(places.ccstatusline.parent.glob("*.deckhand-*")) != []
    assert len(list(places.ccstatusline.parent.glob("*.deckhand-*"))) == 1


@pytest.mark.parametrize("text", ["{broken", "[]", '{"edits": [{"path": 1}]}'])
def test_remove_refuses_a_broken_state_file(places: Places, tmp_path: Path, text: str):
    owner_setup(places, "other")
    statusline.install(places, NOW)
    (places.config / "statusline.json").write_text(text)
    before = snapshot(tmp_path)
    with pytest.raises(Refusal, match="statusline.json"):
        statusline.remove(places, NOW)
    assert snapshot(tmp_path) == before


def test_a_reinstall_after_switching_tools_still_removes_the_old_widget(places: Places):
    owner_setup(places, "ccstatusline")
    widget_before = places.ccstatusline.read_bytes()
    statusline.install(places, NOW)
    settings_before = with_command(places, "printf 'my line'")
    statusline.install(places, NOW)
    statusline.remove(places, NOW)
    assert places.ccstatusline.read_bytes() == widget_before
    assert places.settings.read_bytes() == settings_before


def launch(places: Places, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    command = [str(places.config / statusline.LAUNCHER), *args]
    return subprocess.run(command, input=stdin, capture_output=True, text=True, check=False)


def test_the_launcher_runs_the_newest_version_with_every_argument(places: Places, tmp_path: Path):
    root = cache_root(tmp_path, "1.2.0", "1.10.0")
    places = Places(**{**places.__dict__, "root": root})
    assert statusline.launch(places) == [f"wrote {places.config / statusline.LAUNCHER}"]
    assert statusline.launch(places) == []
    found = launch(places, "captain", "context", "--board", "--watch", "it's")
    assert (found.stdout, found.returncode) == ("1.10.0 captain context --board --watch it's\n", 0)
    cache_root(tmp_path, "1.11.0")
    assert launch(places, "x").stdout == "1.11.0 x\n"


def test_the_launcher_keeps_stdin_and_has_no_time_bound(places: Places):
    (places.root / "bin" / "deckhand").write_text('#!/bin/sh\nsleep 1.5\nread line\necho "$line $*"\n')
    statusline.launch(places)
    started = time.monotonic()
    found = launch(places, "a", "b", stdin="typed\n")
    assert (found.stdout, found.returncode) == ("typed a b\n", 0)
    assert time.monotonic() - started >= 1.5


def test_the_launcher_says_so_when_no_version_is_installed(places: Places, tmp_path: Path):
    root = cache_root(tmp_path, "1.0.0")
    places = Places(**{**places.__dict__, "root": root})
    statusline.launch(places)
    (root / ".orphaned_at").write_text("1")
    found = launch(places, "captain")
    assert (found.stdout, found.returncode) == ("", 1)
    assert found.stderr == "deckhand: no installed deckhand plugin was found\n"


def test_installing_the_status_line_writes_the_launcher_too(places: Places):
    with_command(places, "printf 'my line'")
    statusline.install(places, NOW)
    assert launch(places, "captain", "context", "--line").stdout == PLAIN + "\n"
