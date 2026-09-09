"""Every rule a story body has to meet, and the refusal `checked` raises when one is broken."""

from __future__ import annotations

from pathlib import Path

import pytest

from deckhand import lint, sections
from deckhand.config import BODY_LIMIT
from deckhand.step import Refusal

FIXTURES = Path(__file__).parent / "fixtures"
VALID = (FIXTURES / "body-valid.md").read_text(encoding="utf-8")
INVALID = (FIXTURES / "body-invalid.md").read_text(encoding="utf-8")


def test_valid_body_passes():
    assert lint.lint(VALID) == []


def test_invalid_body_lists_every_failing_rule():
    failures = "\n".join(lint.lint(INVALID))
    assert "missing section: Plan" in failures
    assert "Story is not one" in failures
    assert "Scope: '#### Out' must be non-empty; it is the fence scope creep is measured against" in failures
    assert "not Given/When/Then: Guests see only" in failures
    assert "process line" in failures


def test_a_body_whose_plan_is_folded_passes():
    body = sections.render(*sections.parse(VALID))
    assert "<details>" in body
    assert lint.lint(body) == []


def test_checked_returns_a_body_that_passes():
    body = lint.checked(VALID)
    assert body.count("<details>") == 1
    assert sections.get(body, "Plan") == sections.get(VALID, "Plan")
    assert lint.lint(body) == []


def test_checked_refuses_a_body_that_fails_with_every_rule_on_one_line():
    with pytest.raises(Refusal) as refused:
        lint.checked(INVALID)
    assert str(refused.value).startswith("body: ")
    assert "; ".join(lint.lint(INVALID)) in str(refused.value)


def test_checked_measures_the_body_as_it_is_written():
    body = VALID + "x" * (BODY_LIMIT - len(VALID))
    assert len(body) == BODY_LIMIT
    assert lint.lint(body) == []

    with pytest.raises(Refusal) as refused:
        lint.checked(body)
    assert f"the limit is {BODY_LIMIT}" in str(refused.value)


def test_a_missing_section_reports_only_itself():
    failures = lint.lint(INVALID)
    assert [f for f in failures if "Plan" in f] == ["missing section: Plan"]


def test_an_empty_body_reports_one_message_per_section():
    assert lint.lint("") == [f"missing section: {name}" for name in sections.SECTIONS]


def test_scope_without_an_in_subheading_is_reported():
    body = sections.replace(VALID, "Scope", "#### Out\n\n- Admin UI for managing grants")
    failures = lint.lint(body)
    assert "Scope: no '#### In' subheading" in failures
    assert not any("'#### In' has no bullets" in f for f in failures)


def test_scope_without_an_out_subheading_is_reported():
    body = sections.replace(VALID, "Scope", "#### In\n\n- Filtering")
    failures = lint.lint(body)
    assert "Scope: no '#### Out' subheading" in failures
    assert not any("must be non-empty" in f for f in failures)


def test_scope_in_must_come_before_out():
    body = sections.replace(VALID, "Scope", "#### Out\n\n- Admin UI\n\n#### In\n\n- Filtering")
    failures = lint.lint(body)
    assert failures == ["Scope: '#### In' must come before '#### Out'"]


def test_an_unknown_heading_folds_into_the_section_above():
    criteria = sections.get(VALID, "Acceptance Criteria") + "\n\n### Foo\n\nstray prose"
    body = sections.replace(VALID, "Acceptance Criteria", criteria)

    assert lint.lint(body) == []
    assert [name for name, _ in sections.parse(body)[1]] == sections.SECTIONS
    assert "### Foo\n\nstray prose" in sections.get(body, "Acceptance Criteria")


def test_plan_must_have_a_task_heading():
    body = sections.replace(VALID, "Plan", "**Goal:** land the guest-grant filter")
    failures = lint.lint(body)
    assert any("Plan: no '### Task 1'" in f for f in failures)

    empty = sections.replace(VALID, "Plan", "")
    assert any("Plan: no '### Task 1'" in f for f in lint.lint(empty))


def test_notes_may_be_empty():
    assert lint.lint(sections.replace(VALID, "Notes", "")) == []


def test_a_duplicated_section_is_reported():
    body = VALID + "\n### Notes\n\n- again\n"
    failures = lint.lint(body)
    assert any("duplicate section: Notes" in f for f in failures)


def test_sections_out_of_order_are_reported():
    _, parsed = sections.parse(VALID)
    swapped = [parsed[1], parsed[0], *parsed[2:]]
    failures = lint.lint(sections.render("", swapped))
    assert any("out of order" in f for f in failures)


def test_oversized_body_fails():
    body = sections.replace(VALID, "Notes", "x" * 70000)
    failures = lint.lint(body)
    assert any("limit is 65536" in f for f in failures)


def test_a_body_of_exactly_the_limit_passes():
    base = sections.replace(VALID, "Notes", "x")
    need = 65536 - len(base) + 1
    body = sections.replace(VALID, "Notes", "x" * need)
    assert len(body) == 65536
    assert lint.lint(body) == []


def test_a_crlf_body_of_exactly_the_limit_passes():
    base = sections.replace(VALID, "Notes", "x")
    need = BODY_LIMIT - len(base) + 1
    body = sections.replace(VALID, "Notes", "x" * need).replace("\n", "\r\n")
    assert len(body) > BODY_LIMIT
    assert lint.lint(body) == []


def test_a_crlf_body_over_the_limit_reports_the_lf_character_count():
    base = sections.replace(VALID, "Notes", "x")
    need = BODY_LIMIT - len(base) + 2
    body = sections.replace(VALID, "Notes", "x" * need)
    assert len(body) == BODY_LIMIT + 1
    expected = f"body is {BODY_LIMIT + 1} characters; the limit is {BODY_LIMIT}"
    assert expected in lint.lint(body)
    assert expected in lint.lint(body.replace("\n", "\r\n"))


def test_legitimate_bullets_about_commits_prs_and_ci_are_not_process_lines():
    ac = (
        "- Given a commit message, when the hook runs, then the trailer is appended\n"
        "- Given an open PR list, when the page renders, then each PR title is shown\n"
        "- Given a CI/CD pipeline entry, when the page renders, then it is listed"
    )
    body = sections.replace(VALID, "Acceptance Criteria", ac)
    assert lint.lint(body) == []


def test_a_wrapped_multi_line_bullet_passes():
    ac = "- Given a guest with one grant,\n  when they list collections,\n  then only that collection is returned"
    body = sections.replace(VALID, "Acceptance Criteria", ac)
    assert lint.lint(body) == []


def test_colon_and_bold_given_when_then_pass():
    ac = (
        "- Given: a guest, When: they list, Then: the list is filtered\n"
        "- **Given** a guest, **when** they list, **then** the list is filtered"
    )
    body = sections.replace(VALID, "Acceptance Criteria", ac)
    assert lint.lint(body) == []


def test_a_sub_bullet_with_a_scope_exclusion_is_caught():
    ac = (
        "- Given a guest with one grant, when they list collections, then only that collection is returned\n"
        "  - this is out of scope"
    )
    body = sections.replace(VALID, "Acceptance Criteria", ac)
    failures = lint.lint(body)
    assert any("scope exclusion" in f for f in failures)


def test_a_two_sentence_story_fails():
    body = sections.replace(VALID, "Story", "As a guest, I want a list, so that I see it. Also rebuild the admin UI.")
    failures = lint.lint(body)
    assert any("Story is not one" in f for f in failures)


def test_a_story_with_a_period_inside_a_backticked_so_that_clause_passes():
    body = sections.replace(
        VALID, "Story", "As a developer, I want the parser fixed, so that `sections.py` behaves predictably."
    )
    assert lint.lint(body) == []


def test_a_one_sentence_story_wrapped_across_two_lines_passes():
    body = sections.replace(VALID, "Story", "As a guest, I want to see the list,\nso that I know what is granted.")
    assert lint.lint(body) == []


def test_a_non_form_story_still_fails():
    body = sections.replace(VALID, "Story", "Guests should see fewer collections.")
    failures = lint.lint(body)
    assert any("Story is not one" in f for f in failures)


def test_empty_in_and_empty_acceptance_criteria_are_reported():
    body = sections.replace(VALID, "Scope", "#### In\n\n#### Out\n\n- Admin UI for managing grants")
    body = sections.replace(body, "Acceptance Criteria", "Nothing here.")
    failures = "\n".join(lint.lint(body))
    assert "'#### In' has no bullets" in failures
    assert "Acceptance Criteria has no bullets" in failures
