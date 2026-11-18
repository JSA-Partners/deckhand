from __future__ import annotations

from typing import Literal

from deckhand.credentials import Credentials
from deckhand.linear.client import LinearClient, Transport, default_transport
from deckhand.linear.oauth import fetch_app_token


def connect(
    credentials: Credentials, role: Literal["app", "person"], transport: Transport = default_transport
) -> LinearClient:
    if role == "person":
        return LinearClient(credentials.personal_key, lambda: None, transport, str(credentials.key_path))

    def bearer() -> str:
        return "Bearer " + credentials.app_token(lambda id, secret: fetch_app_token(id, secret, transport))

    return LinearClient(bearer, credentials.invalidate_app_token, transport, str(credentials.app_path))
