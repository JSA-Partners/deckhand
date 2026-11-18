from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

from deckhand.linear.client import LinearError, Transport, default_transport

TOKEN_URL = "https://api.linear.app/oauth/token"
SCOPE = "read,write"


def fetch_app_token(client_id: str, client_secret: str, transport: Transport = default_transport) -> tuple[str, int]:
    form = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": SCOPE,
        }
    ).encode()
    request = urllib.request.Request(
        TOKEN_URL,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        body = json.loads(transport(request))
        token, expires_in = body["access_token"], int(body["expires_in"])
    except urllib.error.HTTPError as error:
        raise LinearError("token", f"the token request failed with HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise LinearError("network", "could not reach Linear") from None
    except (ValueError, KeyError, TypeError):
        raise LinearError("token", "the token response was not usable") from None
    if not isinstance(token, str):
        raise LinearError("token", "the token response was not usable")
    return token, expires_in
