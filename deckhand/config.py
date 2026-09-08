"""Settings: the project comes from the repository's GitHub project link, with an optional override."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from deckhand import gh
from deckhand.gh import LinkedProject

# Every conventional commit type, from the spec at conventionalcommits.org v1.0.0: it mandates feat
# and fix alone and points at the Angular convention for the rest, which is where the other nine
# come from. A subject `finish` accepts opens with one of these.
TYPES = ["build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test"]
# The story-sized subset of TYPES: the Kind a board carries, so the ones a whole story can be.
DEFAULT_KINDS = ["feat", "fix", "chore", "refactor", "docs", "perf"]
BODY_LIMIT = 65536
SETTINGS_FILE = Path(".claude") / "settings.json"


class ConfigError(Exception):
    """A setting is missing or malformed; the message says where to fix it."""


class NoProject(ConfigError):
    """The repository has no project linked, so deckhand has nothing to work against."""


def _default_cache() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "deckhand"


@dataclass
class Settings:
    """Resolved deckhand settings; the project comes from the repository's link on GitHub.

    `/deckhand:setup` links the repository to its project (`gh project link`), and every command reads
    the link back. `DECKHAND_PROJECT` in the environment, or in the optional `env` block of a
    repository's `.claude/settings.json`, overrides the link; that override is only needed when a
    repository is linked to more than one project. Nothing has to be committed to a repository.

    Resolution is lazy and memoized: `project` and `owner` reach GitHub the first time they need to
    and never again in the process.
    """

    override: str | None = None
    kinds: list[str] = field(default_factory=lambda: list(DEFAULT_KINDS))
    cache: Path = field(default_factory=_default_cache)
    resolved: LinkedProject | None = None

    @property
    def project(self) -> int:
        return self.require_project()

    @property
    def owner(self) -> str:
        """The login that owns the project, taken from the repository's link."""
        return self.resolve().owner

    @property
    def owner_type(self) -> str:
        """`Organization` or `User`; project queries pick their root field from it."""
        return self.resolve().owner_type

    def require_project(self) -> int:
        """The project number; an override answers it without asking GitHub anything."""
        if self.resolved is not None:
            return self.resolved.number
        if self.override is not None:
            return _override_number(self.override)
        return self.resolve().number

    def resolve(self) -> LinkedProject:
        """The project this repository works against, resolved once and kept."""
        if self.resolved is None:
            self.resolved = _resolve(self.override)
        return self.resolved


def _override_number(override: str) -> int:
    if not override.isdigit():
        raise ConfigError(f"DECKHAND_PROJECT must be the project number, got {override!r}")
    return int(override)


def _resolve(override: str | None) -> LinkedProject:
    number = None if override is None else _override_number(override)
    repo = gh.repo_slug()
    linked = gh.linked_projects(repo)
    if number is not None:
        return _override_project(repo, number, linked)
    if not linked:
        raise NoProject(f"no project is linked to {repo}; run /deckhand:setup")
    if len(linked) > 1:
        listing = "\n".join(f"  {project}" for project in linked)
        raise ConfigError(
            f"{len(linked)} projects are linked to {repo}; "
            f"set DECKHAND_PROJECT to the number of the one you want:\n{listing}"
        )
    return linked[0]


def _override_project(repo: str, number: int, linked: list[LinkedProject]) -> LinkedProject:
    """The overridden project: the matching link, else a lookup under the repository's own owner."""
    for project in linked:
        if project.number == number:
            return project
    try:
        return gh.project_lookup(repo, number)
    except gh.GhError as error:
        raise ConfigError(
            f"DECKHAND_PROJECT is {number}, which is not among the first 20 projects linked to "
            f"{repo}, and looking it up under the repository owner failed: {error}; "
            f"link it with `gh project link {number} --owner OWNER --repo {repo}` "
            "or set DECKHAND_PROJECT to a linked project's number"
        ) from error


def _settings_env(project_dir: Path) -> dict[str, str]:
    path = project_dir / SETTINGS_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from error
    env = data.get("env", {}) if isinstance(data, dict) else {}
    return {
        k: str(v)
        for k, v in env.items()
        if isinstance(k, str) and isinstance(v, (str, int, float)) and not isinstance(v, bool)
    }


def load(project_dir: Path | None = None) -> Settings:
    """Read settings for the project at `project_dir` (default: the working directory)."""
    project_dir = project_dir or Path.cwd()
    file_env = _settings_env(project_dir)

    def get(name: str) -> str | None:
        return os.environ.get(name) or file_env.get(name) or None

    kinds = (get("DECKHAND_KINDS") or "").split() or list(DEFAULT_KINDS)
    settings = Settings(override=get("DECKHAND_PROJECT"), kinds=kinds)
    cache = get("DECKHAND_CACHE")
    if cache:
        settings.cache = Path(cache).expanduser()
    return settings
