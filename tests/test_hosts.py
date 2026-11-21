from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from deckhand import hosts

SEGMENT = shlex.quote("/home/a b/.config/deckhand/statusline-segment")


def never(text: str) -> bool:
    return False


@pytest.mark.parametrize(
    ("command", "owner"),
    [
        ("", "none"),
        ("  ", "none"),
        ("'/opt/node' '/h/.claude/plugins/claude-hud/statusline.mjs'", "claude-hud"),
        ("npx -y ccstatusline@latest", "ccstatusline"),
        ("bunx -y ccstatusline@latest", "ccstatusline"),
        ("~/.claude/ccline/ccline", "ccometixline"),
        ("npx -y @owloops/claude-powerline@latest --style=powerline", "other"),
        ("~/.claude/statusline.sh", "other"),
    ],
)
def test_owner_reads_the_command(command: str, owner: str):
    assert hosts.owner(command) == owner


@pytest.mark.parametrize(
    "command",
    [
        "node /h/claude-hud/dist/index.js",
        "'/opt/node' '/h/.claude/plugins/claude-hud/statusline.mjs'",
        "'/opt/my node' '/h/a b/.claude/plugins/claude-hud/statusline.mjs'",
        'exec node "$HOME/.claude/plugins/claude-hud/statusline.mjs"',
        "bash -c 'exec node \"${d}dist/index.js\"'",
        "sh -c 'node /h/claude-hud/dist/index.js  '",
    ],
)
def test_hud_appends_the_extra_cmd_to_the_node_call(command: str):
    edited = hosts.hud(command, SEGMENT)
    assert edited is not None
    tokens = shlex.split(edited)
    assert tokens[0] == "CLAUDE_HUD_ALLOW_EXTRA_CMD=1"
    node_args = shlex.split(tokens[-1]) if tokens[-2] == "-c" else tokens
    assert node_args[-2:] == ["--extra-cmd", SEGMENT]


@pytest.mark.parametrize(
    "command",
    [
        "cd /tmp && node /h/claude-hud/dist/index.js",
        "node /h/claude-hud/dist/index.js | cat",
        "node /h/claude-hud/dist/index.js # hud",
        "node /h/claude-hud/dist/index.js --compact",
        "bash -c 'exec node /h/claude-hud/dist/index.js; echo done'",
        'bash -c "exec node /h/claude-hud/dist/index.js"',
        "node 'unterminated",
    ],
)
def test_hud_leaves_other_shapes_to_the_wrapper(command: str):
    assert hosts.hud(command, SEGMENT) is None


def test_ccstatusline_file_follows_its_config_flag(tmp_path: Path):
    default = tmp_path / "default.json"
    assert hosts.ccstatusline_file("npx -y ccstatusline@latest", default) == default
    assert hosts.ccstatusline_file("ccstatusline --config /etc/cc.json", default) == Path("/etc/cc.json")
    assert hosts.ccstatusline_file("ccstatusline --config=/etc/cc.json", default) == Path("/etc/cc.json")
    assert hosts.ccstatusline_file("ccstatusline --config cc.json", default) is None


def test_widget_reports_an_existing_deckhand_widget():
    raw = json.dumps({"lines": [[{"id": "deckhand-captain", "type": "custom-command"}]]}).encode()
    assert hosts.widget(raw, SEGMENT, never) == (True, None)


@pytest.mark.parametrize("raw", [None, b"", b"{", b"[]", b'{"lines": []}', b'{"lines": {"a": 1}}'])
def test_widget_refuses_a_config_without_lines(raw: bytes | None):
    assert hosts.widget(raw, SEGMENT, never) == (False, None)


def test_drop_widget_removes_only_deckhands_items():
    data = {
        "lines": [
            [{"id": "1", "type": "model"}, {"id": "deckhand-captain-separator", "type": "separator"}],
            [{"id": "deckhand-captain", "type": "custom-command"}],
        ],
        "colorLevel": 2,
    }
    assert json.loads(hosts.drop_widget(data, b"{}")) == {
        "lines": [[{"id": "1", "type": "model"}], []],
        "colorLevel": 2,
    }


def test_cmd_item_keeps_the_text_and_adds_one_line():
    raw = b'version = 2\n[[line]]\nleft = ["model"]'
    installed, after = hosts.cmd_item(raw, SEGMENT, never)
    assert not installed
    assert after == raw + b"\n" + hosts.ccline_block(SEGMENT).encode()


def test_cmd_item_reports_an_existing_segment():
    raw = b'[[line]]\nleft = ["model"]\n'
    assert hosts.cmd_item(raw, SEGMENT, lambda text: True) == (True, None)
