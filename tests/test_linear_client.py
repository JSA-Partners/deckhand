from __future__ import annotations

import http.client
import io
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from deckhand.credentials import APP_FILE
from deckhand.linear.client import DuplicateId, KeepAliveTransport, LinearClient, LinearError
from deckhand.linear.oauth import fetch_app_token
from deckhand.step import Refusal

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


def test_second_401_refuses_naming_the_source_without_the_token() -> None:
    unauthorized = Unauthorized()
    transport = Transport(http_error(401), http_error(401))
    with pytest.raises(Refusal) as raised:
        LinearClient(lambda: BEARER, unauthorized, transport, "the key file").query("q", {})
    assert raised.value.rule == "unauthorized: check the key file"
    assert unauthorized.calls == 1


def test_token_not_in_error_text() -> None:
    cases = [
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


@pytest.mark.parametrize("status", [400, 401])
def test_token_endpoint_refusal_names_app_file(status: int) -> None:
    with pytest.raises(Refusal) as raised:
        fetch_app_token("id1", "sec1", Transport(http_error(status, {"error": "invalid_client"})))
    assert APP_FILE in raised.value.rule
    assert "id1" not in raised.value.rule
    assert "sec1" not in raised.value.rule


def test_token_endpoint_server_error_stays_a_linear_error() -> None:
    with pytest.raises(LinearError) as raised:
        fetch_app_token("id1", "sec1", Transport(http_error(500)))
    assert raised.value.code == "token"
    assert "sec1" not in str(raised.value)


def test_truncated_body_is_network_error() -> None:
    with pytest.raises(LinearError) as raised:
        client(Transport(http.client.IncompleteRead(b""))).query("q", {})
    assert raised.value.code == "network"


def test_truncated_error_body_is_linear_error() -> None:
    class Truncated(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise http.client.IncompleteRead(b"")

    error = urllib.error.HTTPError("https://api.linear.app/graphql", 500, "x", {}, Truncated())
    with pytest.raises(LinearError):
        client(Transport(error)).query("q", {})


def test_fetch_app_token_truncated_body_is_network_error() -> None:
    with pytest.raises(LinearError) as raised:
        fetch_app_token("id1", "sec1", Transport(http.client.IncompleteRead(b"")))
    assert raised.value.code == "network"


def test_token_unfit_for_a_header_is_linear_error_without_the_token() -> None:
    def validating(request: urllib.request.Request) -> bytes:
        connection = http.client.HTTPConnection("localhost")
        connection.putrequest("POST", "/graphql")
        for name, value in request.header_items():
            connection.putheader(name, value)
        return ok({})

    leaky = LinearClient(lambda: "lin_api_first\nlin_api_second", Unauthorized(), validating)
    with pytest.raises(LinearError) as raised:
        leaky.query("q", {})
    assert "lin_api" not in str(raised.value)
    assert raised.value.__context__ is None or raised.value.__suppress_context__


def test_after_a_network_failure_later_queries_fail_without_sending() -> None:
    transport = Transport(urllib.error.URLError("down"), ok({"x": 1}))
    linear = client(transport)
    with pytest.raises(LinearError) as first:
        linear.query("q", {})
    with pytest.raises(LinearError) as second:
        linear.query("q", {})
    assert second.value is first.value
    assert second.value.code == "network"
    assert len(transport.requests) == 1


def test_an_http_error_does_not_latch() -> None:
    linear = client(Transport(http_error(500), ok({"x": 1})))
    with pytest.raises(LinearError) as raised:
        linear.query("q", {})
    assert raised.value.code == "http"
    assert linear.query("q", {}) == {"x": 1}


class Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.reason = "x"
        self.headers = http.client.HTTPMessage()
        self.body = body

    def read(self) -> bytes:
        return self.body


class Connection:
    def __init__(self, responses: list[Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, bytes | None, dict]] = []
        self.closed = False

    def request(self, method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> None:
        self.requests.append((method, url, body, dict(headers or {})))

    def getresponse(self) -> Response:
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        self.closed = True


class Connections:
    def __init__(self, *scripts: list[Response | Exception]) -> None:
        self.scripts = list(scripts)
        self.made: list[Connection] = []

    def __call__(self) -> Connection:
        connection = Connection(self.scripts.pop(0))
        self.made.append(connection)
        return connection


def test_keep_alive_reuses_one_connection() -> None:
    connections = Connections([Response(200, ok({"n": n})) for n in range(3)])
    linear = client(KeepAliveTransport(connections))
    assert [linear.query("q", {}) for _ in range(3)] == [{"n": 0}, {"n": 1}, {"n": 2}]
    assert len(connections.made) == 1
    requests = connections.made[0].requests
    assert len(requests) == 3
    assert requests[0][:2] == ("POST", "/graphql")
    assert requests[0][3]["Authorization"] == BEARER


def test_keep_alive_reconnects_once_after_a_dropped_connection() -> None:
    connections = Connections(
        [Response(200, ok({"n": 0})), http.client.RemoteDisconnected("closed")],
        [Response(200, ok({"n": 1}))],
    )
    linear = client(KeepAliveTransport(connections))
    assert linear.query("q", {}) == {"n": 0}
    assert linear.query("q", {}) == {"n": 1}
    assert len(connections.made) == 2
    assert connections.made[0].closed


def test_keep_alive_a_drop_on_a_fresh_connection_is_a_network_failure() -> None:
    connections = Connections([http.client.RemoteDisconnected("closed")], [Response(200, ok({}))])
    with pytest.raises(LinearError) as raised:
        client(KeepAliveTransport(connections)).query("q", {})
    assert raised.value.code == "network"
    assert len(connections.made) == 1


def test_keep_alive_keeps_the_status_handling() -> None:
    unauthorized = Unauthorized()
    connections = Connections(
        [
            Response(401, b""),
            Response(200, ok({"x": 1})),
            Response(400, json.dumps(DUPLICATE).encode()),
            Response(503, b"<html>"),
        ]
    )
    linear = client(KeepAliveTransport(connections), unauthorized)
    assert linear.query("q", {}) == {"x": 1}
    assert unauthorized.calls == 1
    with pytest.raises(DuplicateId):
        linear.query("q", {})
    with pytest.raises(LinearError) as raised:
        linear.query("q", {})
    assert raised.value.code == "http"
    assert len(connections.made) == 1


def test_a_network_failure_fetching_the_token_latches() -> None:
    calls = []

    def token() -> str:
        calls.append(1)
        raise LinearError("network", "could not reach Linear")

    transport = Transport(ok({}))
    linear = LinearClient(token, Unauthorized(), transport)
    with pytest.raises(LinearError) as first:
        linear.query("q", {})
    with pytest.raises(LinearError) as second:
        linear.query("q", {})
    assert second.value is first.value
    assert len(calls) == 1
    assert transport.requests == []
