from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from deckhand import inputs, launch
from deckhand.step import Refusal


def test_a_hand_may_hand_in_a_file_from_the_sandbox_temporary_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, scratch = tmp_path / "claude-501", tmp_path / "scratch"
    state = sandbox / "state"
    state.mkdir(parents=True)
    scratch.mkdir()
    monkeypatch.setattr(launch, "sandbox_tmp", lambda: [sandbox])
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setenv("DECKHAND_ROLE", "hand")
    monkeypatch.setenv("DECKHAND_STATE_DIR", str(state))
    (sandbox / "question.md").write_text("Which queue?")
    assert inputs.read(str(sandbox / "question.md"), "--file", "") == "Which queue?"
    (state / "question.md").write_text("Which queue?")
    with pytest.raises(Refusal, match=inputs.CONFINED):
        inputs.read(str(state / "question.md"), "--file", "")
