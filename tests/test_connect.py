from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from deckhand.credentials import Credentials
from deckhand.linear.connect import connect
from deckhand.linear.oauth import TOKEN_URL
from deckhand.step import Refusal

SECRET = "s3cr3t-value"
KEY = "lin_api_personalvalue"


class Linear:
    def __init__(self, unauthorized: bool = False) -> None:
        self.unauthorized = unauthorized
        self.tokens = 0
        self.authorizations: list[str] = []

    def __call__(self, request: urllib.request.Request) -> bytes:
        if request.full_url == TOKEN_URL:
            self.tokens += 1
            return json.dumps({"access_token": f"app-token-{self.tokens}", "expires_in": 86400}).encode()
        self.authorizations.append(request.get_header("Authorization"))
        if self.unauthorized:
            raise urllib.error.HTTPError(request.full_url, 401, "x", {}, io.BytesIO(b""))
        return json.dumps({"data": {"viewer": {"id": "u1"}}}).encode()


@pytest.fixture
def credentials(tmp_path: Path) -> Credentials:
    for name, text in (
        ("linear-app.json", json.dumps({"client_id": "cid", "client_secret": SECRET})),
        ("linear-personal.key", KEY),
    ):
        (tmp_path / name).write_text(text)
        (tmp_path / name).chmod(0o600)
    return Credentials.load(tmp_path)


def test_app_role_sends_the_bearer_token(credentials: Credentials) -> None:
    linear = Linear()
    assert connect(credentials, "app", linear).query("q", {}) == {"viewer": {"id": "u1"}}
    assert linear.authorizations == ["Bearer app-token-1"]


def test_person_role_sends_the_bare_key(credentials: Credentials) -> None:
    linear = Linear()
    connect(credentials, "person", linear).query("q", {})
    assert linear.authorizations == [KEY]


def test_app_role_refetches_once_then_refuses_naming_the_app_file(credentials: Credentials) -> None:
    linear = Linear(unauthorized=True)
    with pytest.raises(Refusal) as caught:
        connect(credentials, "app", linear).query("q", {})
    assert linear.tokens == 2
    assert linear.authorizations == ["Bearer app-token-1", "Bearer app-token-2"]
    assert caught.value.rule == f"unauthorized: check {credentials.app_path}"
    assert "app-token" not in caught.value.rule and SECRET not in caught.value.rule


def test_person_role_retries_once_then_refuses_naming_the_key_file(credentials: Credentials) -> None:
    linear = Linear(unauthorized=True)
    with pytest.raises(Refusal) as caught:
        connect(credentials, "person", linear).query("q", {})
    assert len(linear.authorizations) == 2
    assert caught.value.rule == f"unauthorized: check {credentials.key_path}"
    assert KEY not in caught.value.rule
