from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from deckhand.linear.client import DuplicateId, LinearClient, LinearError
from deckhand.linear.oauth import fetch_app_token

BEARER = "Bearer app-token-value"

DUPLICATE = {
    "errors": [
        {
            "message": "conflict on insert of Issue",
            "extensions": {
                "type": "invalid input",
                "code": "INPUT_ERROR",
                "statusCode": 400,
                "userError": True,
                "userPresentableMessage": "Entity Issue with id abc already exists.",
            },
        }
    ],
    "data": None,
}


def ok(data: dict) -> bytes:
    return json.dumps({"data": data}).encode()


def http_error(status: int, body: dict | None = None) -> urllib.error.HTTPError:
    payload = json.dumps(body).encode() if body is not None else b""
    return urllib.error.HTTPError("https://api.linear.app/graphql", status, "x", {}, io.BytesIO(payload))


class Transport:
    def __init__(self, *responses: bytes | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request) -> bytes:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Unauthorized:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def client(transport: Transport, unauthorized: Unauthorized | None = None) -> LinearClient:
    return LinearClient(lambda: BEARER, unauthorized or Unauthorized(), transport)


def test_returns_data() -> None:
    transport = Transport(ok({"viewer": {"id": "u1"}}))
    assert client(transport).query("query { viewer { id } }", {"a": 1}) == {"viewer": {"id": "u1"}}
    request = transport.requests[0]
    assert request.get_header("Authorization") == BEARER
    assert json.loads(request.data) == {"query": "query { viewer { id } }", "variables": {"a": 1}}


def test_graphql_error_raises_with_code() -> None:
    body = {"errors": [{"message": "bad", "extensions": {"code": "FORBIDDEN"}}]}
    with pytest.raises(LinearError) as raised:
        client(Transport(json.dumps(body).encode())).query("q", {})
    assert raised.value.code == "FORBIDDEN"
    assert raised.value.message == "bad"


def test_duplicate_id_raises_duplicate() -> None:
    with pytest.raises(DuplicateId) as raised:
        client(Transport(json.dumps(DUPLICATE).encode())).query("q", {})
    assert raised.value.entity == "Issue"
    assert raised.value.code == "INPUT_ERROR"


def test_duplicate_id_in_a_400_body() -> None:
    with pytest.raises(DuplicateId):
        client(Transport(http_error(400, DUPLICATE))).query("q", {})


def test_other_400_is_linear_error() -> None:
    body = {"errors": [{"message": "bad", "extensions": {"code": "INPUT_ERROR"}}]}
    with pytest.raises(LinearError) as raised:
        client(Transport(http_error(400, body))).query("q", {})
    assert not isinstance(raised.value, DuplicateId)
    assert raised.value.code == "INPUT_ERROR"


def test_unreadable_body_is_linear_error() -> None:
    with pytest.raises(LinearError) as raised:
        client(Transport(b"<html>")).query("q", {})
    assert raised.value.code == "bad_response"


def test_401_calls_on_unauthorized_once_then_retries_once() -> None:
    unauthorized = Unauthorized()
    transport = Transport(http_error(401), ok({"x": 1}))
    assert client(transport, unauthorized).query("q", {}) == {"x": 1}
    assert unauthorized.calls == 1
    assert len(transport.requests) == 2


def test_second_401_raises() -> None:
    unauthorized = Unauthorized()
    with pytest.raises(LinearError) as raised:
        client(Transport(http_error(401), http_error(401)), unauthorized).query("q", {})
    assert raised.value.code == "unauthorized"
    assert unauthorized.calls == 1


def test_token_not_in_error_text() -> None:
    cases = [
        Transport(http_error(401), http_error(401)),
        Transport(urllib.error.URLError(f"failed {BEARER}")),
        Transport(http_error(500, {"errors": []})),
    ]
    for transport in cases:
        with pytest.raises(LinearError) as raised:
            client(transport).query("q", {})
        assert "app-token-value" not in str(raised.value)
        assert "app-token-value" not in raised.value.message


def test_network_error_is_linear_error() -> None:
    with pytest.raises(LinearError) as raised:
        client(Transport(urllib.error.URLError("down"))).query("q", {})
    assert raised.value.code == "network"


def test_fetch_app_token_posts_client_credentials() -> None:
    transport = Transport(json.dumps({"access_token": "tok", "expires_in": 86400}).encode())
    assert fetch_app_token("id1", "sec1", transport) == ("tok", 86400)
    request = transport.requests[0]
    assert request.full_url == "https://api.linear.app/oauth/token"
    assert urllib.parse.parse_qs(request.data.decode()) == {
        "grant_type": ["client_credentials"],
        "client_id": ["id1"],
        "client_secret": ["sec1"],
        "scope": ["read,write"],
    }


def test_fetch_app_token_failure_hides_secret() -> None:
    with pytest.raises(LinearError) as raised:
        fetch_app_token("id1", "sec1", Transport(http_error(400, {"error": "invalid_client"})))
    assert raised.value.code == "token"
    assert "sec1" not in str(raised.value)
