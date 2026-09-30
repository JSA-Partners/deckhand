"""The after step: a merged story ticks what its plan asked for, then reaches Done."""

from __future__ import annotations

import json
from pathlib import Path

from deckhand import sections
from tests.conftest import FIXTURES, run_deckhand

STATUS = "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTSSF_STATUS"
TWO = "### Task 1\n\nDo it.\n\n### After the merge\n\n- [ ] Deploy it\n- [ ] Check the dashboard\n"
ONE_DONE = "### Task 1\n\nDo it.\n\n### After the merge\n\n- [x] Deploy it\n- [ ] Check the dashboard\n"
NONE = "### Task 1\n\nDo it.\n"


def _story(tmp_path: Path, plan: str, state: str = "CLOSED") -> dict[str, str]:
    """The approved-story fixture with `plan` as its Plan and `state` as the issue's state."""
    data = json.loads((FIXTURES / "issue-approved.json").read_text(encoding="utf-8"))
    preamble, parsed = sections.parse(data["body"])
    data["body"] = sections.render(preamble, [(n, plan if n == "Plan" else b) for n, b in parsed])
    data["state"] = state
    path = tmp_path / "story.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


def test_apply_ticks_the_item_and_writes_verification(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--item", "1", env=_story(tmp_path, TWO))

    assert result.returncode == 0, result.stderr
    assert f"{STATUS} --single-select-option-id opt_verification" in gh_calls()
    assert any(call.startswith("issue edit 248") for call in gh_calls())


def test_apply_ticks_an_item_under_a_deeper_heading(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "bodies.md"
    env = {**_story(tmp_path, TWO.replace("### After", "#### After")), "GH_BODY_FILE_COPY": str(copy)}

    result = run_deckhand("after", "apply", "248", "--item", "1", env=env)

    assert result.returncode == 0, result.stderr
    assert "#### After the merge\n\n- [x] Deploy it\n- [ ] Check the dashboard" in copy.read_text(encoding="utf-8")


def test_apply_writes_done_when_the_last_box_is_ticked(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--item", "2", env=_story(tmp_path, ONE_DONE))

    assert result.returncode == 0, result.stderr
    assert f"{STATUS} --single-select-option-id opt_done" in gh_calls()


def test_apply_writes_done_at_once_when_the_plan_asks_for_nothing(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", env=_story(tmp_path, NONE))

    assert result.returncode == 0, result.stderr
    assert f"{STATUS} --single-select-option-id opt_done" in gh_calls()


def test_apply_refuses_while_the_issue_is_open(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--item", "1", env=_story(tmp_path, TWO, state="OPEN"))

    assert result.returncode == 1
    assert "open" in result.stderr
    assert not any("item-edit" in call for call in gh_calls())


def test_apply_refuses_an_item_the_plan_does_not_have(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--item", "7", env=_story(tmp_path, TWO))

    assert result.returncode == 1
    assert not any("item-edit" in call for call in gh_calls())


def test_apply_refuses_an_item_already_ticked(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--item", "1", env=_story(tmp_path, ONE_DONE))

    assert result.returncode == 1
    assert not any("item-edit" in call for call in gh_calls())


def test_context_lists_the_items_and_their_boxes(fake_gh, tmp_path):
    result = run_deckhand("after", "context", "248", env=_story(tmp_path, ONE_DONE))

    assert result.returncode == 0, result.stderr
    assert "Deploy it" in result.stdout
    assert "Check the dashboard" in result.stdout
