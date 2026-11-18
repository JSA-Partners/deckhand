from __future__ import annotations

import pytest

from deckhand import decisions, questions


def test_built_question_has_no_recommended_option_and_short_header():
    q = questions.build("scope", "ACME-1", "Which scope?", ["Narrow", "Wide"])
    assert q.header == f"dh {q.nonce}"
    assert len(q.nonce) == 8 and len(q.header) <= 12
    shaped = questions.as_tool_input(q)
    (item,) = shaped["questions"]
    assert item["question"] == "Which scope?"
    assert item["header"] == q.header
    assert item["multiSelect"] is False
    assert [o["label"] for o in item["options"]] == ["Narrow", "Wide"]
    assert all(o["description"] for o in item["options"])
    assert "recommended" not in str(shaped).lower()


def test_nonces_differ_between_questions():
    a = questions.build("scope", "ACME-1", "Q?", ["A", "B"])
    b = questions.build("scope", "ACME-1", "Q?", ["A", "B"])
    assert a.nonce != b.nonce


@pytest.mark.parametrize("count", [0, 1, 5, 6])
def test_build_refuses_option_counts_the_dialog_cannot_show(count: int):
    with pytest.raises(ValueError, match="2 to 4 options"):
        questions.build("scope", "ACME-1", "Q?", [f"Option {n}" for n in range(count)])


@pytest.mark.parametrize("kind", decisions.KINDS)
def test_every_decision_kind_fits_the_question_dialog(kind: str):
    pin = decisions.Pin("Checkout", "v1", ["s1"], label="Checkout", story_labels=["ACME-1"])
    shaped = questions.as_tool_input(decisions.make_question(kind, pin))
    assert 1 <= len(shaped["questions"]) <= 4
    for item in shaped["questions"]:
        assert 2 <= len(item["options"]) <= 4
        assert len(item["header"]) <= 12
