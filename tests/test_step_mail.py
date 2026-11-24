from __future__ import annotations

import json
from pathlib import Path

import pytest

from deckhand.step import Refusal, hand_may_apply
from deckhand.steps.mail import Mail
from deckhand.store.state import LocalState


@pytest.fixture(autouse=True)
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path))
    return tmp_path / "deckhand.db"


def posted(db: Path) -> None:
    with LocalState(db) as rows:
        rows.post("k1", "hand-ACME-1", "answer", "The person decided sign-off: Not yet\nReason: Tax first.")
        rows.post("k2", "hand-ACME-2", "answer", "not yours")
        rows.post("k3", "hand-ACME-1", "nudge", "Carry on.")


def pending(db: Path) -> list[tuple[int, str]]:
    with LocalState(db) as rows:
        return [(m.seq, m.hand) for m in rows.pending()]


def test_mail_context_prints_pending_messages_oldest_first(capsys, db: Path):
    posted(db)
    Mail().context(["ACME-1"])
    assert capsys.readouterr().out.splitlines() == [
        "1 answer",
        "The person decided sign-off: Not yet",
        "Reason: Tax first.",
        "3 nudge",
        "Carry on.",
    ]
    Mail().context(["ACME-1", "--json"])
    shown = json.loads(capsys.readouterr().out)
    assert [(m["seq"], m["kind"]) for m in shown["messages"]] == [(1, "answer"), (3, "nudge")]


def test_mail_context_says_when_no_message_waits(capsys):
    Mail().context(["ACME-1"])
    assert capsys.readouterr().out == "no message waits\n"


def test_ack_clears_through_the_seq_and_prints_the_count(capsys, db: Path):
    posted(db)
    Mail().apply(["ACME-1", "ack", "3"])
    assert capsys.readouterr().out == "done: acknowledged 2\n"
    assert pending(db) == [(2, "hand-ACME-2")]


@pytest.mark.parametrize("seq", ["2", "9", "x", ""])
def test_ack_of_another_hands_seq_refuses_and_writes_nothing(db: Path, seq: str):
    posted(db)
    before = pending(db)
    with pytest.raises(Refusal, match="pending"):
        Mail().apply(["ACME-1", "ack", seq])
    assert pending(db) == before


def test_mail_apply_takes_only_ack(db: Path):
    posted(db)
    with pytest.raises(Refusal, match="mail takes REF"):
        Mail().apply(["ACME-1", "clear", "1"])
    assert len(pending(db)) == 3


def test_a_hand_may_apply_mail_for_its_own_story_only(monkeypatch):
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STORY", "ACME-1")
    assert hand_may_apply("mail", ["ACME-1", "ack", "3"])
    assert not hand_may_apply("mail", ["ACME-2", "ack", "3"])


def as_spike(monkeypatch: pytest.MonkeyPatch, key: str = "a1b2c3d4e5f6") -> None:
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_HAND_KIND", "spike")
    monkeypatch.setenv("DECKHAND_SPIKE", key)
    monkeypatch.delenv("DECKHAND_STORY", raising=False)


def test_a_spike_hand_reads_and_acks_its_own_mail(capsys, db: Path, monkeypatch):
    as_spike(monkeypatch)
    with LocalState(db) as rows:
        rows.post("k1", "spike-a1b2c3d4e5f6", "answer", "The person answered the spike.")
        rows.post("k2", "hand-a1b2c3d4e5f6", "answer", "not the spike's")
    assert hand_may_apply("mail", ["a1b2c3d4e5f6", "ack", "1"])
    assert not hand_may_apply("mail", ["ACME-1", "ack", "1"])
    Mail().context(["a1b2c3d4e5f6"])
    assert capsys.readouterr().out.splitlines() == ["1 answer", "The person answered the spike."]
    Mail().apply(["a1b2c3d4e5f6", "ack", "1"])
    assert capsys.readouterr().out == "done: acknowledged 1\n"
    assert pending(db) == [(2, "hand-a1b2c3d4e5f6")]
