from __future__ import annotations

import http.client
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable

from deckhand.step import Refusal

HOST = "api.linear.app"
URL = f"https://{HOST}/graphql"
TIMEOUT_SECONDS = 30
CONFLICT_PREFIX = "conflict on insert of"

Transport = Callable[[urllib.request.Request], bytes]


class LinearError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class DuplicateId(LinearError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)
        self.entity = message.removeprefix(CONFLICT_PREFIX).strip()


DROPPED = (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError)


def _connect() -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(HOST, timeout=TIMEOUT_SECONDS)


class KeepAliveTransport:
    def __init__(self, connect: Callable[[], http.client.HTTPSConnection] = _connect) -> None:
        self._connect = connect
        self._connection: http.client.HTTPSConnection | None = None

    def __call__(self, request: urllib.request.Request) -> bytes:
        reused = self._connection is not None
        try:
            return self._exchange(request)
        except DROPPED:
            if not reused:
                raise
            return self._exchange(request)

    def _exchange(self, request: urllib.request.Request) -> bytes:
        if self._connection is None:
            self._connection = self._connect()
        try:
            self._connection.request(
                request.get_method(), request.selector, body=request.data, headers=dict(request.header_items())
            )
            response = self._connection.getresponse()
            body = response.read()
        except BaseException:
            self._connection.close()
            self._connection = None
            raise
        if not 200 <= response.status < 300:
            raise urllib.error.HTTPError(
                request.full_url, response.status, response.reason, response.headers, io.BytesIO(body)
            )
        return body


default_transport = KeepAliveTransport()


def _error_from(errors: list) -> LinearError:
    first = errors[0] if errors and isinstance(errors[0], dict) else {}
    extensions = first.get("extensions")
    code = extensions.get("code") if isinstance(extensions, dict) else None
    code = code if isinstance(code, str) else "graphql"
    message = first.get("message")
    message = message if isinstance(message, str) else "request failed"
    if code == "INPUT_ERROR" and message.startswith(CONFLICT_PREFIX):
        return DuplicateId(code, message)
    return LinearError(code, message)


def _parse(raw: bytes) -> dict:
    try:
        body = json.loads(raw)
    except ValueError:
        raise LinearError("bad_response", "the response was not JSON") from None
    if not isinstance(body, dict):
        raise LinearError("bad_response", "the response was not an object")
    return body


class LinearClient:
    def __init__(
        self,
        token: Callable[[], str],
        on_unauthorized: Callable[[], None],
        transport: Transport = default_transport,
        source: str = "the Linear credentials",
    ) -> None:
        self._token = token
        self._on_unauthorized = on_unauthorized
        self._transport = transport
        self._source = source
        self._down: LinearError | None = None

    def query(self, document: str, variables: dict) -> dict:
        if self._down is not None:
            raise self._down
        payload = json.dumps({"query": document, "variables": variables}).encode()
        try:
            raw = self._post(payload)
        except LinearError as error:
            if error.code == "network":
                self._down = error
            raise
        body = _parse(raw)
        errors = body.get("errors")
        if errors:
            raise _error_from(errors if isinstance(errors, list) else [])
        data = body.get("data")
        if not isinstance(data, dict):
            raise LinearError("bad_response", "the response held no data")
        return data

    def _post(self, payload: bytes) -> bytes:
        try:
            return self._send(payload)
        except _Unauthorized:
            self._on_unauthorized()
            try:
                return self._send(payload)
            except _Unauthorized:
                raise Refusal(f"unauthorized: check {self._source}") from None

    def _send(self, payload: bytes) -> bytes:
        request = urllib.request.Request(
            URL,
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": self._token()},
        )
        try:
            return self._transport(request)
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise _Unauthorized from None
            try:
                body = error.read()
            except (OSError, http.client.HTTPException):
                body = b""
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("errors"):
                return body
            raise LinearError("http", f"Linear answered HTTP {error.code}") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            raise LinearError("network", "could not reach Linear") from None
        except ValueError:
            raise LinearError("credentials", "the credential cannot be sent as a header") from None


class _Unauthorized(Exception):
    pass
