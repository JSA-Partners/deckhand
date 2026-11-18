from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from deckhand.credentials import Credentials
from deckhand.step import Refusal

SECRET = "s3cr3t-value"
KEY = "lin_api_personalvalue"


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Fetch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, client_id: str, client_secret: str) -> tuple[str, int]:
        self.calls.append((client_id, client_secret))
        return f"t{len(self.calls)}", 3600


def write_private(path: Path, text: str, mode: int = 0o600) -> None:
    path.write_text(text)
    path.chmod(mode)


@pytest.fixture
def config(tmp_path: Path) -> Path:
    write_private(tmp_path / "linear-app.json", json.dumps({"client_id": "cid", "client_secret": SECRET}))
    write_private(tmp_path / "linear-personal.key", KEY + "\n")
    return tmp_path


def test_missing_file_refuses_with_path(config: Path) -> None:
    (config / "linear-app.json").unlink()
    with pytest.raises(Refusal) as caught:
        Credentials.load(config)
    assert "linear-app.json" in caught.value.rule


def test_world_readable_file_refuses(config: Path) -> None:
    (config / "linear-app.json").chmod(0o644)
    with pytest.raises(Refusal) as caught:
        Credentials.load(config)
    assert "chmod 600" in caught.value.rule
    assert "linear-app.json" in caught.value.rule
    assert SECRET not in caught.value.rule


def test_world_readable_personal_key_refuses(config: Path) -> None:
    (config / "linear-personal.key").chmod(0o640)
    with pytest.raises(Refusal) as caught:
        Credentials.load(config)
    assert "chmod 600" in caught.value.rule
    assert KEY not in caught.value.rule


def test_malformed_app_file_refuses_without_the_secret(config: Path) -> None:
    write_private(config / "linear-app.json", json.dumps({"client_secret": SECRET}))
    with pytest.raises(Refusal) as caught:
        Credentials.load(config)
    assert "linear-app.json" in caught.value.rule
    assert SECRET not in caught.value.rule


def test_personal_key_is_stripped(config: Path) -> None:
    assert Credentials.load(config).personal_key() == KEY


def test_token_cached_until_near_expiry(config: Path) -> None:
    fetch = Fetch()
    creds = Credentials.load(config, now=Clock())
    assert creds.app_token(fetch) == "t1"
    assert creds.app_token(fetch) == "t1"
    assert fetch.calls == [("cid", SECRET)]


def test_token_cache_survives_a_new_load(config: Path) -> None:
    fetch = Fetch()
    clock = Clock()
    Credentials.load(config, now=clock).app_token(fetch)
    assert Credentials.load(config, now=clock).app_token(fetch) == "t1"
    assert len(fetch.calls) == 1


def test_token_refetched_within_five_minutes_of_expiry(config: Path) -> None:
    clock = Clock()
    write_private(
        config / "app-token.json",
        json.dumps({"access_token": "old", "expires_at": clock.now + 240}),
    )
    fetch = Fetch()
    assert Credentials.load(config, now=clock).app_token(fetch) == "t1"
    assert len(fetch.calls) == 1


def test_token_kept_beyond_five_minutes_of_expiry(config: Path) -> None:
    clock = Clock()
    write_private(
        config / "app-token.json",
        json.dumps({"access_token": "old", "expires_at": clock.now + 400}),
    )
    fetch = Fetch()
    assert Credentials.load(config, now=clock).app_token(fetch) == "old"
    assert fetch.calls == []


def test_unreadable_cache_is_refetched(config: Path) -> None:
    write_private(config / "app-token.json", "not json")
    fetch = Fetch()
    assert Credentials.load(config, now=Clock()).app_token(fetch) == "t1"


def test_invalidate_forces_refetch(config: Path) -> None:
    fetch = Fetch()
    creds = Credentials.load(config, now=Clock())
    creds.app_token(fetch)
    creds.invalidate_app_token()
    assert creds.app_token(fetch) == "t2"
    assert len(fetch.calls) == 2


def test_cache_file_is_owner_only(config: Path) -> None:
    Credentials.load(config, now=Clock()).app_token(Fetch())
    mode = stat.S_IMODE(os.stat(config / "app-token.json").st_mode)
    assert mode == 0o600


def test_cache_file_tightened_when_it_already_existed_open(config: Path) -> None:
    write_private(config / "app-token.json", "{}", mode=0o644)
    Credentials.load(config, now=Clock()).app_token(Fetch())
    assert stat.S_IMODE(os.stat(config / "app-token.json").st_mode) == 0o600


def test_secret_never_in_repr(config: Path) -> None:
    creds = Credentials.load(config, now=Clock())
    token = creds.app_token(Fetch())
    for text in (repr(creds), str(creds)):
        assert SECRET not in text
        assert KEY not in text
        assert token not in text
