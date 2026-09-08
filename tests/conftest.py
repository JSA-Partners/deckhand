"""Shared fixtures: a fake gh on PATH, a scratch repo, and an isolated cache and config."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from deckhand import cli, gh
from deckhand.config import Settings

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_repo_slug_cache() -> None:
    """repo_slug() is memoized in the process; clear it before each test so fixtures don't leak across tests."""
    gh.repo_slug.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Settings already resolved to acme #2, so a unit test needs no project-link lookup."""
    return Settings(resolved=gh.LinkedProject(owner="acme", owner_type="Organization", number=2, title="Widgets"))


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear ambient DECKHAND_* env vars so tests are isolated from the environment they run in."""
    for name in list(os.environ):
        if name.startswith("DECKHAND_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the ambient git repository and the user's git config from every test.

    git exports GIT_DIR, GIT_INDEX_FILE, GIT_PREFIX, and friends to its hooks, so a hook-invoked
    pytest must never see the ambient repository: a test that inits a scratch repo would otherwise
    re-init the real one and write its config, index, and objects there. Only GIT_EXEC_PATH survives.
    """
    for name in list(os.environ):
        if name.startswith("GIT_") and name != "GIT_EXEC_PATH":
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)


@pytest.fixture(scope="session")
def _no_real_gh_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the failing `gh` stub once per session; tests only need to put it on PATH."""
    stub_dir = tmp_path_factory.mktemp("no-real-gh")
    script = stub_dir / "gh"
    script.write_text('#!/bin/sh\necho "unstubbed gh call: $*" >&2\nexit 1\n')
    script.chmod(0o755)
    return stub_dir


@pytest.fixture(autouse=True)
def no_real_gh(_no_real_gh_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put a `gh` stub first on PATH that fails loudly, so a test that forgets `fake_gh` errors out."""
    monkeypatch.setenv("PATH", f"{_no_real_gh_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
def fake_gh(no_real_gh: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put tests/fakes/gh first on PATH and point it at the fixtures; returns the calls log path."""
    if not (ROOT / "tests" / "fakes" / "gh").exists():
        pytest.fail("tests/fakes/gh is missing")
    calls = tmp_path / "gh-calls"
    monkeypatch.setenv("PATH", f"{ROOT / 'tests' / 'fakes'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_FIXTURES", str(FIXTURES))
    monkeypatch.setenv("GH_CALLS", str(calls))
    monkeypatch.setenv("DECKHAND_CACHE", str(tmp_path / "cache"))
    return calls


@pytest.fixture
def gh_calls(fake_gh: Path):
    """A function that returns the recorded gh invocations, one per line."""
    return lambda: fake_gh.read_text().splitlines() if fake_gh.exists() else []


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An initialized git repository with one commit, as the working directory."""
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "f").write_text("x\n")
    subprocess.run(["git", "add", "f"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    monkeypatch.chdir(path)
    return path


@pytest.fixture
def registry():
    """Snapshot and restore `deckhand.cli._REGISTRY`, so an in-process test can register commands.

    Discovery runs lazily the first time a parser is built, so import the discovery module first;
    otherwise an early snapshot would restore an empty registry and hide every real command.
    """
    import deckhand.commands  # noqa: F401  (side effect: registers every command module)

    original = dict(cli._REGISTRY)
    try:
        yield cli._REGISTRY
    finally:
        cli._REGISTRY.clear()
        cli._REGISTRY.update(original)


def run_deckhand(
    *args: str,
    stdin: str | None = None,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run bin/deckhand as a subprocess, the way a skill or workflow does."""
    return subprocess.run(
        [str(ROOT / "bin" / "deckhand"), *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=cwd,
        env={**os.environ, **env} if env is not None else None,
    )
