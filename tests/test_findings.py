"""The findings, verdicts and decisions parser: the shape all three files share."""

from __future__ import annotations

import pytest

from deckhand import findings
from deckhand.step import Refusal

KNOWN = {"chaos", "unknowns"}


def test_a_well_formed_findings_line_parses():
    text = "chaos.1 | P2 | PENDING | A retried job writes the grant twice | Scope In names one write\n"

    (found,) = findings.findings(text, KNOWN)

    assert found.lens == "chaos"
    assert found.ordinal == 1
    assert found.severity == "P2"
    assert found.claim == "A retried job writes the grant twice"
    assert found.evidence == "Scope In names one write"


def test_a_claim_with_a_bare_pipe_still_parses():
    """A regex alternation in backticks has no spaces around its pipe, so the spaced split still gives five fields."""
    line = "chaos.1 | P2 | PENDING | The pattern `^(a|b)$` admits an empty match. | store.go:14"

    found = findings._parse(line)

    assert found is not None
    assert found.claim == "The pattern `^(a|b)$` admits an empty match."
    assert found.evidence == "store.go:14"


def test_a_line_written_without_spaces_still_parses():
    found = findings._parse("chaos.1|P2|PENDING|A claim.|store.go:14")

    assert found is not None
    assert found.claim == "A claim."


def test_a_verdict_reason_may_contain_a_bare_pipe():
    assert findings._parse_verdict("chaos.1 | REJECTED | The guard reads `a|b` already.") == (
        "chaos.1",
        "REJECTED",
        "The guard reads `a|b` already.",
    )


def test_a_spaced_pipe_in_the_claim_is_refused_and_names_the_line():
    text = "chaos.1 | P1 | PENDING | foo(a | b) is never bounded | Scope In\n"

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert str(refused.value).startswith("line 1: expected ")


def test_four_fields_instead_of_five_is_refused():
    text = "chaos.1 | P2 | PENDING | a claim with no evidence\n"

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert str(refused.value).startswith("line 1: expected ")


def test_every_malformed_findings_line_is_named_in_one_refusal():
    """Nine lines written four fields wide cost nine refusals; the repair needs them all at once."""
    text = (
        "chaos.1 | P2 | PENDING | a claim with no evidence\n"
        "chaos.2 | P1 | PENDING | another claim folded in\n"
        "chaos.3 | P2 | PENDING | A claim | store.go:14\n"
    )

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert str(refused.value).splitlines() == [
        "line 1: expected <lens>.<n> | P1|P2|P3 | PENDING | <claim> | <evidence>; found 4 fields",
        "line 2: found 4 fields",
    ]


def test_an_unknown_lens_is_refused_by_the_line_that_carries_it():
    text = "chaos.1 | P2 | PENDING | A claim | store.go:14\nvibes.1 | P2 | PENDING | A claim | store.go:14\n"

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert str(refused.value) == "line 2: no lens named 'vibes'"


def test_the_expected_shape_rides_on_the_first_malformed_line_whatever_came_before_it():
    """A file whose first fault is semantic still has to be told the shape the rest was meant to take."""
    text = "vibes.1 | P2 | PENDING | A claim | store.go:14\nchaos.2 | P1 | PENDING | a claim with no evidence\n"

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert str(refused.value).splitlines() == [
        "line 1: no lens named 'vibes'",
        "line 2: expected <lens>.<n> | P1|P2|P3 | PENDING | <claim> | <evidence>; found 4 fields",
    ]
    assert str(refused.value).count("expected ") == 1


def test_a_verdicts_file_passed_as_findings_names_the_shape_it_reads_as():
    text = "chaos.1 | CONFIRMED\n"

    with pytest.raises(Refusal) as refused:
        findings.findings(text, KNOWN)

    assert "reads as verdicts" in str(refused.value)
    assert "findings, verdicts, decisions" in str(refused.value)


def test_a_rejected_verdict_with_no_reason_is_refused():
    found = findings.findings(
        "chaos.1 | P2 | PENDING | A retried job writes the grant twice | Scope In names one write\n", KNOWN
    )

    with pytest.raises(Refusal) as refused:
        findings.verdicts("chaos.1 | REJECTED\n", found)

    assert str(refused.value).startswith("line 1: expected ")


def test_clean_as_the_whole_findings_file_is_accepted():
    assert findings.findings(f"{findings.CLEAN}\n", KNOWN) == []
