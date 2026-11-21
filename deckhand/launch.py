from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deckhand import git
from deckhand.config import LocalConfig
from deckhand.policy import Policy
from deckhand.step import Refusal

HAND_EXEMPT = ["deckhand *", "gh pr view *", "gh pr diff *", "gh pr checks *"]
CAPTAIN_EXEMPT = ["deckhand *"]
SECRET_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "LINEAR_API_KEY")
NO_DIALOG = ["AskUserQuestion", "SendMessage", "ListAgents", "mcp__*"]
GIT_CREDENTIALS = (".ssh", ".git-credentials", ".netrc", ".config/git/credentials", ".gnupg")
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
PASSED_DIRS = ("DECKHAND_CONFIG_DIR", "DECKHAND_STATE_DIR")


WILDCARDS = frozenset("*?[")


@dataclass(frozen=True)
class Protected:
    secrets: tuple[Path, ...]
    state: tuple[Path, ...]


def _forms(*paths: Path) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        given = path.expanduser().absolute()
        if WILDCARDS & set(str(given)):
            raise Refusal(f"{given}: a path holding *, ? or [ reads as a wildcard in the sandbox; move it")
        for form in (given, given.resolve()):
            if form not in found:
                found.append(form)
    return found


def protected(config: Path, state: Path) -> Protected:
    home = Path.home() / ".config"
    return Protected(tuple(_forms(config, home / "deckhand", home / "gh")), tuple(_forms(state)))


# Permission rules need a double slash for an absolute path; one slash anchors at the settings source.
def _rule(tool: str, path: Path, suffix: str = "") -> str:
    return f"{tool}(//{str(path).lstrip('/')}{suffix})"


def _guarded(protected: Protected, deny: list[str], deny_write: list[str]) -> dict[str, Any]:
    secrets = [str(path) for path in protected.secrets]
    permissions_deny = [*deny]
    for path in protected.secrets:
        permissions_deny += [_rule("Read", path, "/**"), _rule("Edit", path, "/**")]
    permissions_deny += [_rule("Edit", path, "/**") for path in protected.state]
    state = [str(path) for path in protected.state]
    return {
        "deny": permissions_deny,
        "filesystem": {"denyWrite": secrets + state + deny_write, "denyRead": secrets},
        "credentials": {
            "files": [{"path": path, "mode": "deny"} for path in secrets],
            "envVars": [{"name": name, "mode": "deny"} for name in SECRET_ENV],
        },
    }


def _sandbox(exempt: list[str], filesystem: dict, credentials: dict) -> dict[str, Any]:
    return {
        "enabled": True,
        "allowUnsandboxedCommands": False,
        "autoAllowBashIfSandboxed": True,
        "excludedCommands": exempt,
        "filesystem": filesystem,
        "credentials": credentials,
    }


def _hand_env(ref: str, roots: list[Path], spike: bool) -> dict[str, str]:
    env = {"DECKHAND_ROLE": "hand", ("DECKHAND_SPIKE" if spike else "DECKHAND_STORY"): ref}
    env.update({name: os.environ[name] for name in PASSED_DIRS if os.environ.get(name)})
    plugin_dir = os.environ.get("DECKHAND_PLUGIN_DIR")
    plugin_bin = Path(plugin_dir) / "bin" if plugin_dir else Path(__file__).resolve().parent.parent / "bin"
    env["PATH"] = os.pathsep.join([str(plugin_bin), *filter(None, git.trusted_path(roots).split(os.pathsep))])
    return env


# Exempt commands resolve through PATH, so a writable PATH directory would run planted code unsandboxed.
def _refuse_path_writes(sandbox_write: list[str], search_path: str) -> None:
    dirs = [Path(entry).resolve() for entry in search_path.split(os.pathsep) if entry]
    for entry in sandbox_write:
        cut = min((entry.index(c) for c in WILDCARDS if c in entry), default=len(entry))
        base = Path(entry[:cut] if cut == len(entry) or entry[cut - 1] == "/" else os.path.dirname(entry[:cut]))
        root = base.expanduser().resolve()
        for found in dirs:
            if found.is_relative_to(root):
                raise Refusal(
                    f"sandbox_write entry {entry} holds {found}, a PATH directory a hand could plant a command in"
                )


def hand_settings(
    ref: str,
    clone: Path,
    worktree: Path,
    policy: Policy,
    local: LocalConfig,
    protected: Protected,
    spike: bool = False,
) -> dict[str, Any]:
    clones = _forms(clone)
    worktrees = _forms(worktree)
    env = _hand_env(ref, clones + worktrees, spike)
    _refuse_path_writes(local.sandbox_write, env["PATH"])
    git_credentials = _forms(*(Path.home() / name for name in GIT_CREDENTIALS))
    deny = list(NO_DIALOG)
    deny += [_rule("Read", path, suffix) for path in git_credentials for suffix in ("", "/**")]
    deny += [_rule("Edit", path / ".git", "/**") for path in clones]
    deny += [_rule("Edit", path / ".git") for path in worktrees]
    deny += [_rule("Edit", path / name) for path in worktrees for name in CLAUDE_CONFIG]
    metadata = [str(path / ".git") for path in clones + worktrees]
    guarded = _guarded(protected, deny, metadata)
    filesystem = {"allowWrite": list(local.sandbox_write), **guarded["filesystem"]}
    filesystem["denyRead"] = filesystem["denyRead"] + [str(path) for path in git_credentials]
    sandbox = _sandbox(HAND_EXEMPT, filesystem, guarded["credentials"])
    sandbox["network"] = {"allowedDomains": list(policy.sandbox_domains)}
    return {
        "env": env,
        "permissions": {
            "allow": ["Bash(deckhand *)"],
            "deny": guarded["deny"],
            "blockWritesOutsideWorkingDirectories": True,
            "additionalDirectories": [str(path) for path in _forms(Path(tempfile.gettempdir()))],
        },
        "disableClaudeAiConnectors": True,
        "sandbox": sandbox,
    }


def captain_settings(protected: Protected) -> dict[str, Any]:
    guarded = _guarded(protected, [], [])
    return {
        "env": {"DECKHAND_ROLE": "captain"},
        "viewMode": "focus",
        "permissions": {"allow": ["Bash(deckhand *)", "SendMessage"], "deny": guarded["deny"]},
        "sandbox": _sandbox(CAPTAIN_EXEMPT, guarded["filesystem"], guarded["credentials"]),
    }


def hand_argv(
    name: str, settings: dict[str, Any], prompt: str, plugin_dir: Path | None, resume: str | None
) -> list[str]:
    argv = ["claude", "--bg", "--name", name, "--permission-mode", "auto", "--settings", json.dumps(settings)]
    if plugin_dir is not None:
        argv += ["--plugin-dir", str(plugin_dir)]
    if resume is not None:
        argv += ["--resume", resume]
    return [*argv, prompt]
