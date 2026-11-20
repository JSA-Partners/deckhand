from __future__ import annotations

from pathlib import Path

import pytest

from deckhand import answers, features, spikes
from deckhand.step import Refusal
from deckhand.steps.spike import Spike
from deckhand.store.fake import FakeStore
from tests.test_step_stories import P1, TEAM, setup

QUESTION = "Does the queue keep order?"
ANSWER = "Found: yes, per partition.\nVerified: ran it twice.\nUnsettled: behavior under restart."


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def asked(question: str = QUESTION) -> str:
    key = spikes.key(P1, question)
    path = spikes.question_path(answers.state_dir(), key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(question)
    return key


def answer_file(tmp_path: Path, text: str = ANSWER) -> str:
    path = tmp_path / "answer.md"
    path.write_text(text)
    return str(path)


def test_spike_answer_is_a_project_document(tmp_path: Path):
    store, key = setup(), asked()
    Spike(store).apply([key, "--file", answer_file(tmp_path), *TEAM])
    document = store.document(spikes.answer_id(P1, key))
    assert document is not None
    assert (document["title"], document["body"]) == (f"Spike: {QUESTION}", ANSWER)
    feature = features.open_feature(store, "ACME")
    assert feature is not None
    assert [d["id"] for d in spikes.answered(store, feature)] == [spikes.answer_id(P1, key)]


def test_spike_answer_rerun_writes_nothing(tmp_path: Path):
    store, key = setup(), asked()
    path = answer_file(tmp_path)
    Spike(store).apply([key, "--file", path, *TEAM])
    written = list(store.writes)
    Spike(store).apply([key, "--file", path, *TEAM])
    assert store.writes == written


def test_spike_answer_refuses_a_key_nobody_asked(tmp_path: Path):
    store = setup()
    written = list(store.writes)
    with pytest.raises(Refusal, match="no spike ffffffffffff"):
        Spike(store).apply(["ffffffffffff", "--file", answer_file(tmp_path), *TEAM])
    assert store.writes == written


def test_spike_answer_refuses_an_empty_answer(tmp_path: Path):
    store, key = setup(), asked()
    written = list(store.writes)
    with pytest.raises(Refusal, match="answer is empty"):
        Spike(store).apply([key, "--file", answer_file(tmp_path, "  \n"), *TEAM])
    assert store.writes == written


def test_spike_context_prints_the_question_and_the_answer_shape(capsys: pytest.CaptureFixture[str]):
    key = asked()
    Spike(FakeStore()).context([key])
    out = capsys.readouterr().out
    assert QUESTION in out
    for part in ("what was found", "how it was verified", "what could not be settled"):
        assert part in out
