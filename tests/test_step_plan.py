from __future__ import annotations

import pytest

from deckhand import planning, spikes
from deckhand.steps.plan import Plan
from tests.test_step_decide import P1, feature_store

KEY = "ACME"


def answered(store, body: str = "Yes, see [x.io](<http://x.io>) and a\\_b.") -> str:
    key = spikes.key(P1, "Does the queue keep order?")
    store.put_document(spikes.answer_id(P1, key), P1, "Spike: Does the queue keep order?", body)
    return key


def test_plan_context_lists_and_prints_answered_spikes(capsys: pytest.CaptureFixture[str]):
    store = feature_store()
    plan = Plan(store)
    plan.context(["--team-key", KEY])
    assert "answered spikes" not in capsys.readouterr().out
    key = answered(store)
    plan.context(["--team-key", KEY])
    assert capsys.readouterr().out.endswith(f"answered spikes:\n{key} Spike: Does the queue keep order?\n")
    plan.context(["spike", key, "--team-key", KEY])
    assert capsys.readouterr().out == "Yes, see x.io and a_b.\n"
    plan.context(["spike", "nope", "--team-key", KEY])
    assert capsys.readouterr().out == "no answered spike nope\n"


def test_a_section_still_prints_by_name(capsys: pytest.CaptureFixture[str]):
    store = feature_store()
    Plan(store).context(["Contract", "--team-key", KEY])
    assert capsys.readouterr().out == f"{planning.WRITTEN}\n\nPOST /orders.\n"


def test_plan_context_approval_prints_the_screen(capsys: pytest.CaptureFixture[str]):
    store = feature_store()
    Plan(store).context(["approval", "--team-key", KEY])
    shown = capsys.readouterr().out
    assert shown.startswith(f"# Contract\n\n{planning.WRITTEN}\n\nPOST /orders.\n\n# Examples\n\nnot written\n")
    assert "# Story map\n\n## ACME-1 One\n" in shown
