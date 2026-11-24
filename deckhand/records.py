from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

RECORD_SCHEMA = 1
HAND = "agent:hand"

_FIELDS: dict[str, type] = {"kind": str, "subject": str, "version": str, "author": str, "data": dict}


@dataclass(frozen=True)
class Record:
    kind: str
    subject: str
    version: str
    author: str
    data: dict[str, Any]
    at: str = field(default="", compare=False)

    def to_json(self) -> str:
        payload = {"schema": RECORD_SCHEMA, **{name: getattr(self, name) for name in _FIELDS}}
        return json.dumps(payload, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> Record:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("record is not a JSON object")
        schema = payload.get("schema")
        if schema != RECORD_SCHEMA:
            raise ValueError(f"unsupported record schema {schema}")
        for name, kind in _FIELDS.items():
            if name not in payload:
                raise ValueError(f"record is missing {name!r}")
            if not isinstance(payload[name], kind):
                raise ValueError(f"record field {name!r} must be a {kind.__name__}")
        return cls(**{name: payload[name] for name in _FIELDS})


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iso(ms: int) -> str:
    moment = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


class Clock:
    def __init__(self) -> None:
        self._last = 0

    # Linear stamps sequential writes in order; the wall clock repeats within one millisecond.
    def __call__(self) -> str:
        self._last = max(self._last + 1, time.time_ns() // 1_000_000)
        return iso(self._last)
