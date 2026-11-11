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


def _status_reads(tmp_path: Path, status: str) -> dict[str, str]:
    """An env where the board's field-values read answers `status` for Status."""
    data = json.loads((FIXTURES / "graphql-fieldvalues.json").read_text(encoding="utf-8"))
    for node in data["data"]["node"]["fieldValues"]["nodes"]:
        if (node.get("field") or {}).get("name") == "Status":
            node["name"] = status
    path = tmp_path / f"fieldvalues-{status.lower().replace(' ', '-')}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_FIELDVALUES_FILE": str(path)}


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


def test_apply_add_appends_an_item_and_moves_the_story_to_verification(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "bodies.md"
    env = {**_story(tmp_path, TWO), "GH_BODY_FILE_COPY": str(copy)}

    result = run_deckhand("after", "apply", "248", "--add", "Turn on API keys in production", env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Added item 3: Turn on API keys in production", "Logged Noted"]
    written = copy.read_text(encoding="utf-8")
    assert "- [ ] Check the dashboard\n- [ ] Turn on API keys in production" in written
    assert "Noted: after the merge item added: Turn on API keys in production" in written
    assert f"{STATUS} --single-select-option-id opt_verification" in gh_calls()


def test_apply_add_opens_the_block_when_the_plan_has_none(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "bodies.md"
    env = {**_story(tmp_path, NONE), "GH_BODY_FILE_COPY": str(copy)}

    result = run_deckhand("after", "apply", "248", "--add", "Deploy it", env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "Added item 1: Deploy it"
    assert "### After the merge\n\n- [ ] Deploy it" in copy.read_text(encoding="utf-8")


def test_apply_add_refuses_an_open_story_and_writes_nothing(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--add", "Deploy it", env=_story(tmp_path, TWO, state="OPEN"))

    assert result.returncode == 1
    assert "amend" in result.stderr
    assert not [call for call in gh_calls() if call.startswith(("issue edit", "issue comment")) or "item-edit" in call]


def test_apply_add_and_item_together_is_a_usage_error(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--add", "x", "--item", "1", env=_story(tmp_path, TWO))

    assert result.returncode != 0
    assert gh_calls() == []


WRAPPED = "### Task 1\n\nDo it.\n\n### After the merge\n\n- [ ] Deploy it to the cluster\n  and watch the rollout\n"


def test_apply_add_lands_after_a_wrapped_last_item(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "bodies.md"
    env = {**_story(tmp_path, WRAPPED), "GH_BODY_FILE_COPY": str(copy)}

    result = run_deckhand("after", "apply", "248", "--add", "Check the dashboard", env=env)

    assert result.returncode == 0, result.stderr
    assert "  and watch the rollout\n- [ ] Check the dashboard" in copy.read_text(encoding="utf-8")


def test_apply_add_refuses_blank_or_multi_line_text_and_writes_nothing(fake_gh, gh_calls, tmp_path):
    for text in ("  ", "one\ntwo"):
        result = run_deckhand("after", "apply", "248", "--add", text, env=_story(tmp_path, TWO))

        assert result.returncode == 1
        assert "one line" in result.stderr
    assert not [call for call in gh_calls() if call.startswith(("issue edit", "issue comment")) or "item-edit" in call]


def test_context_opens_with_the_apply(fake_gh, tmp_path):
    env = {**_story(tmp_path, ONE_DONE), **_status_reads(tmp_path, "Verification")}
    result = run_deckhand("after", "context", "248", env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "Apply: deckhand after apply 248 --item 2"
    assert "Then:" not in result.stdout


def test_context_opens_with_the_bare_apply_while_the_story_is_in_review(fake_gh, tmp_path):
    """Only an apply moves the column, and a session that only lists the items runs none."""
    env = {**_story(tmp_path, ONE_DONE), **_status_reads(tmp_path, "In Review")}
    result = run_deckhand("after", "context", "248", env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == [
        "Apply: deckhand after apply 248",
        "Then: deckhand after apply 248 --item 2",
    ]


def test_context_opens_with_the_bare_apply_alone_when_nothing_is_owed(fake_gh, tmp_path):
    env = {**_story(tmp_path, NONE), **_status_reads(tmp_path, "In Review")}
    result = run_deckhand("after", "context", "248", env=env)

    assert result.stdout.splitlines()[0] == "Apply: deckhand after apply 248"
    assert "Then:" not in result.stdout


def test_context_without_the_column_opens_with_the_item(fake_gh, tmp_path):
    env = {**_story(tmp_path, ONE_DONE), "GH_FIELDVALUES_FILE": str(tmp_path / "gone.json")}
    result = run_deckhand("after", "context", "248", env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "Apply: deckhand after apply 248 --item 2"


def test_context_still_names_the_apply_when_the_issue_cannot_be_read(fake_gh, tmp_path):
    result = run_deckhand("after", "context", "248", env={"GH_ISSUE_FILE": str(tmp_path / "gone.json")})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "Apply: deckhand after apply 248"
    assert "could not be read" in result.stdout


def test_context_lists_the_items_and_their_boxes(fake_gh, tmp_path):
    result = run_deckhand("after", "context", "248", env=_story(tmp_path, ONE_DONE))

    assert result.returncode == 0, result.stderr
    assert "Deploy it" in result.stdout
    assert "Check the dashboard" in result.stdout


DROPPABLE = (
    "### Task 1\n\nDo it.\n\n### After the merge\n\n"
    "- [ ] Release it to production\n  with the cutover runbook\n- [ ] Check the dashboard\n- [x] Deploy it\n"
)


def test_drop_takes_one_unticked_item_and_its_continuation_off_the_list(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "bodies.md"
    env = {**_story(tmp_path, DROPPABLE), "GH_BODY_FILE_COPY": str(copy)}

    result = run_deckhand("after", "apply", "248", "--drop", "1", "--note", "parked as #300", env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == ["Dropped item 1: Release it to production", "Logged After the merge"]
    written = copy.read_text(encoding="utf-8")
    assert "### After the merge\n\n- [ ] Check the dashboard\n- [x] Deploy it\n" in written
    assert "Release it to production" not in written.split("--- issue comment")[0]
    assert "with the cutover runbook" not in written
    assert "### Task 1\n\nDo it.\n" in written
    assert "After the merge: dropped item 1: Release it to production. parked as #300" in written
    assert f"{STATUS} --single-select-option-id opt_verification" in gh_calls()


def test_dropping_the_last_open_item_reaches_done(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--drop", "2", "--note", "moved", env=_story(tmp_path, ONE_DONE))

    assert result.returncode == 0, result.stderr
    assert f"{STATUS} --single-select-option-id opt_done" in gh_calls()


def test_drop_refuses_a_ticked_item(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--drop", "1", "--note", "x", env=_story(tmp_path, ONE_DONE))

    assert result.returncode == 1
    assert "item 1 of #248 is ticked; a done item stays on the record" in result.stderr
    assert not [call for call in gh_calls() if call.startswith(("issue edit", "issue comment", "project item-edit"))]


def test_drop_refuses_without_a_note(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--drop", "1", env=_story(tmp_path, TWO))

    assert result.returncode != 0
    assert not [call for call in gh_calls() if call.startswith(("issue edit", "issue comment", "project item-edit"))]


def test_drop_refuses_an_item_out_of_range(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--drop", "5", "--note", "x", env=_story(tmp_path, TWO))

    assert result.returncode == 1
    assert "#248 has 2 after-the-merge items; there is no item 5" in result.stderr
    assert not [call for call in gh_calls() if call.startswith(("issue edit", "issue comment", "project item-edit"))]


def test_drop_refuses_an_open_issue(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("after", "apply", "248", "--drop", "1", "--note", "x", env=_story(tmp_path, TWO, "OPEN"))

    assert result.returncode == 1
    assert not [call for call in gh_calls() if call.startswith(("issue edit", "issue comment", "project item-edit"))]
