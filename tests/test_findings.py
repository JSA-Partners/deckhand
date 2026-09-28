"""The findings, verdicts and decisions parser: the JSON shape all three files share."""

from __future__ import annotations

import json

import pytest

from deckhand import findings
from deckhand.step import Refusal

KNOWN = {"chaos", "unknowns"}


def _doc(kind: str, **entries) -> str:
    return json.dumps({"kind": kind, kind: entries[kind]})


def test_a_well_formed_finding_parses():
    text = _doc(
        "findings",
        findings=[
            {
                "lens": "chaos",
                "ordinal": 1,
                "severity": "P2",
                "claim": "A retried job writes the grant twice",
                "evidence": "Scope In names one write",
            }
        ],
    )

    (found,) = findings.findings(text, KNOWN)

    assert found.lens == "chaos"
    assert found.ordinal == 1
    assert found.severity == "P2"
    assert found.claim == "A retried job writes the grant twice"
    assert found.evidence == "Scope In names one write"
    assert found.verdict == findings.PENDING


def test_an_empty_findings_array_is_a_clean_pass():
    assert findings.findings(_doc("findings", findings=[]), KNOWN) == []


def test_an_unknown_lens_is_refused_naming_the_entry():
    text = _doc(
        "findings",
        findings=[
            {"lens": "chaos", "ordinal": 1, "severity": "P2", "claim": "A claim", "evidence": "store.go:14"},
            {"lens": "vibes", "ordinal": 1, "severity": "P2", "claim": "A claim", "evidence": "store.go:14"},
        ],
    )

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert str(refused.value) == "entry 2: no lens named 'vibes'"


def test_two_findings_with_the_same_lens_and_ordinal_are_refused():
    text = _doc(
        "findings",
        findings=[
            {
                "lens": "chaos",
                "ordinal": 1,
                "severity": "P1",
                "claim": "A retry writes twice",
                "evidence": "Scope In",
            },
            {
                "lens": "chaos",
                "ordinal": 1,
                "severity": "P2",
                "claim": "The cache outlives the row",
                "evidence": "Notes",
            },
        ],
    )

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert str(refused.value) == "entry 2: chaos.1 is already the id of an earlier finding"


def test_a_severity_that_is_not_p1_p2_or_p3_is_refused():
    text = _doc(
        "findings",
        findings=[{"lens": "chaos", "ordinal": 1, "severity": "P4", "claim": "A claim", "evidence": "store.go:14"}],
    )

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert "severity" in str(refused.value)
    assert "entry 1" in str(refused.value)


def test_a_rejected_verdict_with_no_reason_is_refused():
    found = findings.findings(
        _doc(
            "findings",
            findings=[
                {
                    "lens": "chaos",
                    "ordinal": 1,
                    "severity": "P2",
                    "claim": "A retried job writes the grant twice",
                    "evidence": "Scope In names one write",
                }
            ],
        ),
        KNOWN,
    )

    with pytest.raises(Refusal) as refused:
        findings.verdicts(_doc("verdicts", verdicts=[{"id": "chaos.1", "verdict": "REJECTED"}]), found)

    assert "reason" in str(refused.value)
    assert "entry 1" in str(refused.value)


def test_a_verdict_naming_an_id_that_is_not_a_finding_is_refused():
    found = findings.findings(
        _doc(
            "findings",
            findings=[{"lens": "chaos", "ordinal": 1, "severity": "P2", "claim": "A claim", "evidence": "e"}],
        ),
        KNOWN,
    )

    with pytest.raises(Refusal) as refused:
        findings.verdicts(
            _doc(
                "verdicts",
                verdicts=[{"id": "chaos.1", "verdict": "CONFIRMED"}, {"id": "chaos.2", "verdict": "CONFIRMED"}],
            ),
            found,
        )

    assert str(refused.value) == "entry 2: chaos.2 is not a finding"


def test_two_verdicts_for_the_same_finding_are_refused():
    found = findings.findings(
        _doc(
            "findings",
            findings=[{"lens": "chaos", "ordinal": 1, "severity": "P2", "claim": "A claim", "evidence": "e"}],
        ),
        KNOWN,
    )

    with pytest.raises(Refusal) as refused:
        findings.verdicts(
            _doc(
                "verdicts",
                verdicts=[
                    {"id": "chaos.1", "verdict": "CONFIRMED"},
                    {"id": "chaos.1", "verdict": "REJECTED", "reason": "no"},
                ],
            ),
            found,
        )

    assert str(refused.value) == "entry 2: chaos.1 already has a verdict"


def test_a_finding_with_no_verdict_is_refused():
    found = findings.findings(
        _doc(
            "findings",
            findings=[{"lens": "chaos", "ordinal": 1, "severity": "P2", "claim": "A claim", "evidence": "e"}],
        ),
        KNOWN,
    )

    with pytest.raises(Refusal) as refused:
        findings.verdicts(_doc("verdicts", verdicts=[]), found)

    assert str(refused.value) == "no verdict for chaos.1"


def test_a_decision_naming_an_id_that_is_not_a_finding_is_refused():
    found = findings.findings(
        _doc(
            "findings",
            findings=[{"lens": "chaos", "ordinal": 1, "severity": "P2", "claim": "A claim", "evidence": "e"}],
        ),
        KNOWN,
    )

    with pytest.raises(Refusal) as refused:
        findings.decisions(
            _doc(
                "decisions",
                decisions=[
                    {"id": "chaos.1", "decision": "accepted"},
                    {"id": "chaos.9", "decision": "accepted"},
                ],
            ),
            found,
        )

    assert str(refused.value) == "entry 2: chaos.9 is not a finding"


def test_two_decisions_for_the_same_finding_are_refused():
    found = findings.findings(
        _doc(
            "findings",
            findings=[{"lens": "chaos", "ordinal": 1, "severity": "P2", "claim": "A claim", "evidence": "e"}],
        ),
        KNOWN,
    )

    with pytest.raises(Refusal) as refused:
        findings.decisions(
            _doc(
                "decisions",
                decisions=[
                    {"id": "chaos.1", "decision": "accepted"},
                    {"id": "chaos.1", "decision": "declined"},
                ],
            ),
            found,
        )

    assert str(refused.value) == "entry 2: chaos.1 already has a decision"


def test_a_finding_with_no_decision_is_refused():
    found = findings.findings(
        _doc(
            "findings",
            findings=[{"lens": "chaos", "ordinal": 1, "severity": "P2", "claim": "A claim", "evidence": "e"}],
        ),
        KNOWN,
    )

    with pytest.raises(Refusal) as refused:
        findings.decisions(_doc("decisions", decisions=[]), found)

    assert str(refused.value) == "no decision for chaos.1"


# --- what is new with JSON ---------------------------------------------------


def test_a_document_with_the_wrong_kind_is_refused_naming_both():
    text = json.dumps({"kind": "findings", "findings": []})

    with pytest.raises(Refusal) as refused:
        findings.decisions(text, [])

    message = str(refused.value)
    assert "decisions" in message
    assert "findings" in message


def test_text_that_is_not_json_is_refused_with_the_line_and_column():
    with pytest.raises(Refusal) as refused:
        findings.findings("not json at all", KNOWN)

    message = str(refused.value)
    assert "line" in message
    assert "column" in message
    assert "Traceback" not in message


def test_an_entry_missing_a_required_key_names_it_and_its_position():
    text = _doc(
        "findings",
        findings=[
            {"lens": "chaos", "ordinal": 1, "severity": "P2", "claim": "A claim", "evidence": "e"},
            {"lens": "chaos", "ordinal": 2, "severity": "P2", "evidence": "e"},
        ],
    )

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    message = str(refused.value)
    assert "claim" in message
    assert "entry 2" in message


def test_the_list_key_missing_refuses_naming_the_key():
    text = json.dumps({"kind": "findings"})

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert "findings" in str(refused.value)


def test_the_list_key_holding_an_object_refuses_naming_the_key():
    text = json.dumps({"kind": "findings", "findings": {"lens": "chaos"}})

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert "findings" in str(refused.value)


def _finding(lens: str, ordinal: int) -> dict:
    return {
        "lens": lens,
        "ordinal": ordinal,
        "severity": "P2",
        "claim": "A retried job writes the grant twice",
        "evidence": "Scope In names one write",
    }


def test_every_bad_entry_is_named_in_one_refusal():
    """A file wrong the same way throughout costs one run to fix, not one run per entry."""
    text = _doc("findings", findings=[_finding("chaos", 1), {"lens": "chaos"}, {"ordinal": 3}])

    with pytest.raises(Refusal) as raised:
        findings.findings(text, KNOWN)

    said = str(raised.value).splitlines()
    assert len(said) > 2
    assert any("entry 2" in line for line in said)
    assert any("entry 3" in line for line in said)


def test_every_bad_verdict_is_named_in_one_refusal():
    found = findings.findings(_doc("findings", findings=[_finding("chaos", 1), _finding("chaos", 2)]), KNOWN)
    text = _doc(
        "verdicts",
        verdicts=[
            {"id": "chaos.1", "verdict": "CONFIRMED"},
            {"id": "chaos.1", "verdict": "CONFIRMED"},
            {"id": "chaos.9", "verdict": "CONFIRMED"},
        ],
    )

    with pytest.raises(Refusal) as raised:
        findings.verdicts(text, found)

    said = str(raised.value).splitlines()
    assert any("already has a verdict" in line for line in said)
    assert any("chaos.9 is not a finding" in line for line in said)
