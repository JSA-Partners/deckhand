from __future__ import annotations

from deckhand import questions


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
    a = questions.build("scope", "ACME-1", "Q?", ["A"])
    b = questions.build("scope", "ACME-1", "Q?", ["A"])
    assert a.nonce != b.nonce
