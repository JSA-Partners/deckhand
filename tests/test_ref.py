from __future__ import annotations

import pytest

from deckhand.ref import Ref


def test_parse_round_trips():
    assert str(Ref.parse("ACME-12")) == "ACME-12"


def test_any_team_key():
    assert Ref.parse("FOG-3").team == "FOG"
    assert Ref.parse("X9-1").team == "X9"


def test_lowercase_is_normalized():
    assert Ref.parse("acme-12") == Ref("ACME", 12)


@pytest.mark.parametrize("text", ["12", "ACME", "ACME-0", "ACME-1a", ""])
def test_rejects(text):
    with pytest.raises(ValueError, match=f"{text!r}"):
        Ref.parse(text)
