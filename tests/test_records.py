from __future__ import annotations

import json

import pytest

from deckhand.records import Record, content_hash


def _record(**overrides):
    fields = {
        "kind": "plan",
        "subject": "ACME-12",
        "version": "3",
        "author": "mjm",
        "data": {"steps": [{"name": "build", "done": False}], "owner": {"id": 7}},
    }
    fields.update(overrides)
    return Record(**fields)


def test_round_trip():
    r = _record()
    assert Record.from_json(r.to_json()) == r


def test_to_json_is_stable_across_key_order():
    a = _record(data={"x": 1, "y": 2})
    b = _record(data={"y": 2, "x": 1})
    assert a.to_json() == b.to_json()


def test_unknown_schema_refused():
    payload = json.loads(_record().to_json())
    payload["schema"] = 2
    with pytest.raises(ValueError, match="schema 2"):
        Record.from_json(json.dumps(payload))


def test_missing_field_refused():
    payload = json.loads(_record().to_json())
    del payload["kind"]
    with pytest.raises(ValueError, match="kind"):
        Record.from_json(json.dumps(payload))


def test_hash_is_stable():
    assert content_hash("a\n") == content_hash("a\n")
    assert content_hash("a\n") != content_hash("a")


@pytest.mark.parametrize("text", ["[]", "null", "3", '"x"'])
def test_non_object_refused(text):
    with pytest.raises(ValueError, match="not a JSON object"):
        Record.from_json(text)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", 1),
        ("subject", None),
        ("version", 3),
        ("author", ["mjm"]),
        ("data", ["steps"]),
    ],
)
def test_wrong_field_type_refused(field, value):
    payload = json.loads(_record().to_json())
    payload[field] = value
    with pytest.raises(ValueError, match=field):
        Record.from_json(json.dumps(payload))
