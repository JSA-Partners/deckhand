from __future__ import annotations

import http.client
import io
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

import pytest

from deckhand import __version__
from deckhand.credentials import APP_FILE
from deckhand.linear.client import DuplicateId, KeepAliveTransport, LinearClient, LinearError
from deckhand.linear.meter import Meter, clock
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


class Validating(Connection):
    def request(self, method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> None:
        connection = http.client.HTTPConnection("localhost")
        connection.putrequest(method, url)
        for name, value in (headers or {}).items():
            connection.putheader(name, value)
        super().request(method, url, body, headers)


class Connections:
    def __init__(self, *scripts: list[Response | Exception], kind: type[Connection] = Connection) -> None:
        self.scripts = list(scripts)
        self.kind = kind
        self.made: list[Connection] = []

    def __call__(self) -> Connection:
        connection = self.kind(self.scripts.pop(0))
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


def test_keep_alive_reconnects_once_after_a_tls_end_of_file_on_a_reused_connection() -> None:
    connections = Connections(
        [Response(200, ok({"n": 0})), ssl.SSLEOFError("EOF occurred in violation of protocol")],
        [Response(200, ok({"n": 1}))],
    )
    linear = client(KeepAliveTransport(connections))
    assert linear.query("q", {}) == {"n": 0}
    assert linear.query("q", {}) == {"n": 1}
    assert len(connections.made) == 2


def test_keep_alive_a_tls_end_of_file_on_a_fresh_connection_latches() -> None:
    connections = Connections([ssl.SSLEOFError("EOF occurred in violation of protocol")], [Response(200, ok({}))])
    linear = client(KeepAliveTransport(connections))
    for _ in range(2):
        with pytest.raises(LinearError) as raised:
            linear.query("q", {})
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


def test_an_http_error_does_not_latch() -> None:
    connections = Connections([Response(500, b""), Response(200, ok({"x": 1}))])
    linear = client(KeepAliveTransport(connections))
    with pytest.raises(LinearError) as raised:
        linear.query("q", {})
    assert raised.value.code == "http"
    assert linear.query("q", {}) == {"x": 1}


def test_after_a_network_failure_the_transport_fails_without_sending() -> None:
    connections = Connections([ConnectionRefusedError()], [Response(200, ok({}))])
    transport = KeepAliveTransport(connections)
    linear = client(transport)
    for _ in range(2):
        with pytest.raises(LinearError) as raised:
            linear.query("q", {})
        assert raised.value.code == "network"
    assert len(connections.made) == 1
    assert len(connections.made[0].requests) == 1


def test_a_second_client_on_a_down_transport_fails_without_sending() -> None:
    connections = Connections([ConnectionRefusedError()], [Response(200, ok({}))])
    transport = KeepAliveTransport(connections)
    with pytest.raises(LinearError):
        client(transport).query("q", {})
    with pytest.raises(LinearError) as raised:
        LinearClient(lambda: "lin_api_person", Unauthorized(), transport).query("q", {})
    assert raised.value.code == "network"
    assert len(connections.made) == 1


def test_a_down_transport_also_stops_the_token_request() -> None:
    connections = Connections([ConnectionRefusedError()], [Response(200, b"{}")])
    transport = KeepAliveTransport(connections)
    with pytest.raises(LinearError):
        client(transport).query("q", {})
    with pytest.raises(LinearError) as raised:
        fetch_app_token("id1", "sec1", transport)
    assert raised.value.code == "network"
    assert len(connections.made) == 1


def test_reset_brings_a_down_transport_back() -> None:
    connections = Connections([ConnectionRefusedError()], [Response(200, ok({"x": 1}))])
    transport = KeepAliveTransport(connections)
    with pytest.raises(LinearError):
        client(transport).query("q", {})
    transport.reset()
    assert client(transport).query("q", {}) == {"x": 1}


def test_requests_name_deckhand_as_the_user_agent() -> None:
    transport = Transport(ok({}), json.dumps({"access_token": "tok", "expires_in": 1}).encode())
    client(transport).query("q", {})
    fetch_app_token("id1", "sec1", transport)
    assert [r.get_header("User-agent") for r in transport.requests] == [f"deckhand/{__version__}"] * 2


def test_a_token_unfit_for_a_header_through_keep_alive_is_a_credentials_error() -> None:
    connections = Connections([Response(200, ok({}))], kind=Validating)
    leaky = LinearClient(lambda: "lin_api_first\nlin_api_second", Unauthorized(), KeepAliveTransport(connections))
    with pytest.raises(LinearError) as raised:
        leaky.query("q", {})
    assert raised.value.code == "credentials"
    assert "lin_api" not in str(raised.value)
    assert "lin_api" not in raised.value.message


class Proxied:
    made: list[Proxied] = []

    def __init__(self, host: str, port: int | None = None, timeout: float | None = None) -> None:
        self.address = (host, port)
        self.tunnel: str | None = None
        Proxied.made.append(self)

    def set_tunnel(self, host: str) -> None:
        self.tunnel = host

    def request(self, method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> None:
        pass

    def getresponse(self) -> Response:
        return Response(200, ok({}))

    def close(self) -> None:
        pass


@pytest.fixture
def proxied(monkeypatch: pytest.MonkeyPatch) -> list[Proxied]:
    for name in ("https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(http.client, "HTTPSConnection", Proxied)
    monkeypatch.setattr(Proxied, "made", [])
    return Proxied.made


def test_an_https_proxy_tunnels_to_linear(proxied: list[Proxied], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    client(KeepAliveTransport()).query("q", {})
    assert [(made.address, made.tunnel) for made in proxied] == [(("proxy.example", 3128), "api.linear.app")]


def test_no_proxy_for_linear_connects_directly(proxied: list[Proxied], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("NO_PROXY", "api.linear.app")
    client(KeepAliveTransport()).query("q", {})
    assert [(made.address[0], made.tunnel) for made in proxied] == [("api.linear.app", None)]


def limited_error(headers: dict[str, str]) -> urllib.error.HTTPError:
    body = {"errors": [{"message": "Rate limit exceeded.", "extensions": {"code": "RATELIMITED"}}]}
    message = http.client.HTTPMessage()
    for name, value in headers.items():
        message[name] = value
    return urllib.error.HTTPError(
        "https://api.linear.app/graphql", 400, "x", message, io.BytesIO(json.dumps(body).encode())
    )


def test_the_meter_hears_the_headers_of_a_reply_through_keep_alive() -> None:
    response = Response(200, ok({"x": 1}))
    response.headers["X-Complexity"] = "37"
    meter = Meter(lambda: 5.0)
    linear = LinearClient(lambda: BEARER, Unauthorized(), KeepAliveTransport(Connections([response])), meter=meter)
    linear.query("query Issue($id: String!) { issue(id: $id) { id } }", {"id": "a"})
    assert meter.taken()["costs"] == {"Issue": 37}


def test_the_meter_hears_a_rate_limit_from_the_error_headers() -> None:
    meter = Meter(lambda: 5.0)
    transport = Transport(limited_error({"Retry-After": "60"}))
    linear = LinearClient(lambda: BEARER, Unauthorized(), transport, meter=meter)
    with pytest.raises(LinearError) as raised:
        linear.query("query Viewer { viewer { id } }", {})
    assert raised.value.code == "RATELIMITED"
    assert meter.taken()["budget"]["until"] == 65.0


def test_a_rate_limit_names_when_to_run_the_call_again() -> None:
    meter = Meter(lambda: 1_000.0)
    linear = LinearClient(lambda: BEARER, Unauthorized(), Transport(limited_error({"Retry-After": "720"})), meter=meter)
    with pytest.raises(LinearError) as raised:
        linear.query("query Viewer { viewer { id } }", {})
    assert raised.value.code == "RATELIMITED"
    assert raised.value.message == (
        f"Linear's rate limit is spent until {clock(1_720.0)}, in 12 minutes; run the same call again then"
    )


def test_a_rate_limit_without_a_meter_still_names_a_time() -> None:
    with pytest.raises(LinearError) as raised:
        client(Transport(limited_error({}))).query("query Viewer { viewer { id } }", {})
    assert raised.value.message.startswith("Linear's rate limit is spent until ")


def test_a_plain_transport_is_metered_without_headers() -> None:
    meter = Meter(lambda: 5.0)
    LinearClient(lambda: BEARER, Unauthorized(), Transport(ok({})), meter=meter).query("query Viewer { v }", {})
    assert meter.taken() == {"budget": {"at": 5.0, "until": None}, "costs": {}}
