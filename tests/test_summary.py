from __future__ import annotations

import json

import pytest

from deckhand import summary
from deckhand.step import Refusal

GOOD = {
    "intents": [{"title": "Charge once", "lines": ["retries reuse the key", "no double charge"]}],
    "new_here": [{"text": "first use of a queue", "path": "./src/queue.py"}],
    "screenshots": ["shots/a.png"],
    "state_diagram": None,
}


def parse(**changes: object) -> summary.ChangeSummary:
    return summary.parse_summary(json.dumps({**GOOD, **changes}))


def refusal(**changes: object) -> str:
    with pytest.raises(Refusal) as caught:
        parse(**changes)
    return str(caught.value)


def test_parses_a_change_summary() -> None:
    parsed = parse(state_diagram="docs/state.svg")
    assert parsed.intents == [summary.Intent("Charge once", ["retries reuse the key", "no double charge"])]
    assert parsed.new_here == [summary.NewHere("first use of a queue", "src/queue.py")]
    assert parsed.screenshots == ["shots/a.png"]
    assert parsed.state_diagram == "docs/state.svg"
    assert parse().state_diagram is None


def test_refuses_with_index_and_field() -> None:
    assert "intents entry 1 field title" in refusal(intents=[GOOD["intents"][0], {"title": " ", "lines": []}])
    assert "intents entry 0 field lines" in refusal(intents=[{"title": "t", "lines": "x"}])
    assert "new_here entry 0 field path" in refusal(new_here=[{"text": "t", "path": "../x"}])
    assert "new_here entry 0 field text" in refusal(new_here=[{"text": "", "path": "x"}])
    assert "new_here entry 0 has an unknown field extra" in refusal(new_here=[{"text": "t", "path": "x", "extra": 1}])
    assert "screenshots entry 0" in refusal(screenshots=[3])
    assert "state_diagram" in refusal(state_diagram=3)


def test_refuses_bad_documents() -> None:
    for text, part in [("{", "not valid JSON"), ("[]", "JSON object"), ('{"intents": []}', "new_here is missing")]:
        with pytest.raises(Refusal, match=part):
            summary.parse_summary(text)
    with pytest.raises(Refusal, match="unknown field extra"):
        summary.parse_summary(json.dumps({**GOOD, "extra": 1}))
