from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from deckhand.store.fake import FakeStore

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("DECKHAND_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("DECKHAND_CONFIG_DIR", str(tmp_path / "isolated-config"))


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "isolated-state"))


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("GIT_") and name != "GIT_EXEC_PATH":
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)


class CountingStore:
    def __init__(self, inner: FakeStore) -> None:
        self._inner = inner
        self.calls: dict[str, int] = {}
        self.log: list[tuple[str, tuple, tuple]] = []
        self.opens = 0

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._inner, name)

        def counted(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] = self.calls.get(name, 0) + 1
            self.log.append((name, args, tuple(sorted(kwargs.items()))))
            return method(*args, **kwargs)

        return counted


@pytest.fixture
def counting_store() -> Callable[[FakeStore], CountingStore]:
    return CountingStore
