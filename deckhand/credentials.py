from __future__ import annotations

import json
import os
import stat
import time
from collections.abc import Callable
from pathlib import Path

from deckhand.step import Refusal

APP_FILE = "linear-app.json"
KEY_FILE = "linear-personal.key"
TOKEN_FILE = "app-token.json"
REFRESH_MARGIN_SECONDS = 300


def _usable(value: object) -> bool:
    return isinstance(value, str) and value != "" and value.isascii() and value.isprintable()


def _read_private(path: Path) -> str:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise Refusal(f"{path} is readable by others, run: chmod 600 {path}")
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        raise Refusal(f"missing credentials file: {path}") from None
    except OSError as error:
        raise Refusal(f"cannot read {path}: {error.strerror}") from None
    except UnicodeDecodeError:
        raise Refusal(f"{path} is not UTF-8 text") from None


class Credentials:
    def __init__(
        self, config_dir: Path, client_id: str, client_secret: str, key: str, now: Callable[[], float]
    ) -> None:
        self._dir = config_dir
        self._client_id = client_id
        self._client_secret = client_secret
        self._key = key
        self._now = now
        self._token: str | None = None
        self._expires_at = 0.0
        self._read_cache()

    @classmethod
    def load(cls, config_dir: Path, now: Callable[[], float] = time.time) -> Credentials:
        app_path = config_dir / APP_FILE
        try:
            app = json.loads(_read_private(app_path))
            client_id, client_secret = app["client_id"], app["client_secret"]
        except (ValueError, KeyError, TypeError):
            client_id = client_secret = None
        if not _usable(client_id) or not _usable(client_secret):
            raise Refusal(f"{app_path} must hold a client_id and a client_secret, each one line of printable ASCII")
        key_path = config_dir / KEY_FILE
        key = _read_private(key_path).strip()
        if not _usable(key):
            raise Refusal(f"{key_path} must hold one line of printable ASCII")
        return cls(config_dir, client_id, client_secret, key, now)

    def __repr__(self) -> str:
        return f"Credentials({str(self._dir)!r})"

    @property
    def app_path(self) -> Path:
        return self._dir / APP_FILE

    @property
    def key_path(self) -> Path:
        return self._dir / KEY_FILE

    def personal_key(self) -> str:
        return self._key

    def app_token(self, fetch: Callable[[str, str], tuple[str, int]]) -> str:
        if self._token is not None and self._now() < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._token
        token, expires_in = fetch(self._client_id, self._client_secret)
        self._token = token
        self._expires_at = self._now() + expires_in
        self._write_cache()
        return token

    def invalidate_app_token(self) -> None:
        self._token = None
        self._expires_at = 0.0
        (self._dir / TOKEN_FILE).unlink(missing_ok=True)

    def _read_cache(self) -> None:
        path = self._dir / TOKEN_FILE
        try:
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                return
            cached = json.loads(path.read_text())
            token, expires_at = cached["access_token"], float(cached["expires_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        if _usable(token):
            self._token, self._expires_at = token, expires_at

    def _write_cache(self) -> None:
        path = self._dir / TOKEN_FILE
        payload = json.dumps({"access_token": self._token, "expires_at": self._expires_at})
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
