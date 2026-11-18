from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

RECORD_SCHEMA = 1

_FIELDS = ("kind", "subject", "version", "author", "data")


@dataclass(frozen=True)
class Record:
    kind: str
    subject: str
    version: str
    author: str
    data: dict[str, Any]

    def to_json(self) -> str:
        payload = {"schema": RECORD_SCHEMA, **{name: getattr(self, name) for name in _FIELDS}}
        return json.dumps(payload, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> Record:
        payload = json.loads(text)
        schema = payload.get("schema")
        if schema != RECORD_SCHEMA:
            raise ValueError(f"unsupported record schema {schema}")
        for name in _FIELDS:
            if name not in payload:
                raise ValueError(f"record is missing {name!r}")
        return cls(**{name: payload[name] for name in _FIELDS})


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
