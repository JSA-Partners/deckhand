from __future__ import annotations

import http.client
import os
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from deckhand.linear.client import URL, default_transport
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


class Wire:
    def __init__(self) -> None:
        self.up = False
        self.sent = 0

    def send(self) -> bool:
        try:
            default_transport(urllib.request.Request(URL, data=b"{}"))
        except OSError:
            return False
        return True


class _Answer:
    status, reason, headers = 200, "OK", http.client.HTTPMessage()

    def read(self) -> bytes:
        return b'{"data": {}}'


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[Wire]:
    found = Wire()

    class Asleep:
        def __init__(self, host: str, port: int | None = None, timeout: float | None = None) -> None:
            pass

        def set_tunnel(self, host: str) -> None:
            pass

        def request(self, method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> None:
            found.sent += 1
            if not found.up:
                raise ConnectionRefusedError("asleep")

        def getresponse(self) -> _Answer:
            return _Answer()

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", Asleep)
    default_transport.reset()
    yield found
    default_transport.reset()
