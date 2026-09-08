from __future__ import annotations

import json

import pytest

from deckhand import stub
from deckhand.stub import Entry
from tests.conftest import FIXTURES

REQUIREMENTS = "Guests should see only the collections they were granted."
BULLETS = [
    "- Grant store | Persist grants.",
    "- Handler filter | Filter by grant. (after 1)",
    "- Admin view | Show grants. (after 1, 2)",
]
ENTRIES = [
    Entry("Grant store", "Persist grants.", ()),
    Entry("Handler filter", "Filter by grant.", (1,)),
    Entry("Admin view", "Show grants.", (1, 2)),
]
NUMBERED = [
    Entry("Grant store", "Persist grants.", (), 57),
    Entry("Handler filter", "Filter by grant.", (1,), 58),
    Entry("Admin view", "Show grants.", (1, 2), 59),
]


def _split(*bullets: str, requirements: str = REQUIREMENTS) -> str:
    """A split file with `bullets` under Stories; Stories is line 5 and the first bullet is line 7."""
    return "\n".join(["## Requirements", "", requirements, "", "## Stories", "", *bullets]) + "\n"


# --- parse_split ------------------------------------------------------------


def test_parse_split_reads_the_requirements_and_every_bullet():
    requirements, entries = stub.parse_split(_split(*BULLETS))

    assert requirements == REQUIREMENTS
    assert entries == ENTRIES


def test_parse_split_names_the_line_of_a_bullet_without_a_pipe():
    text = _split(BULLETS[0], "- Handler filter filters by grant.", BULLETS[2])

    with pytest.raises(ValueError, match="line 8: expected '<title> | <sentence>'"):
        stub.parse_split(text)


def test_parse_split_refuses_a_pipe_inside_the_title_or_the_sentence():
    text = _split("- Grant | store | Persist grants.")

    with pytest.raises(ValueError, match="line 7: a title or sentence cannot contain"):
        stub.parse_split(text)


def test_parse_split_refuses_an_after_that_names_a_later_entry():
    text = _split(BULLETS[0], "- Handler filter | Filter by grant. (after 3)", BULLETS[2])

    with pytest.raises(ValueError, match="line 8: after 3 names this story or a later one"):
        stub.parse_split(text)


def test_parse_split_refuses_an_after_out_of_range():
    text = _split(BULLETS[0], "- Handler filter | Filter by grant. (after 9)", BULLETS[2])

    with pytest.raises(ValueError, match="line 8: after 9 names no story; there are 3"):
        stub.parse_split(text)


def test_parse_split_refuses_an_after_that_names_itself():
    text = _split(BULLETS[0], "- Handler filter | Filter by grant. (after 2)", BULLETS[2])

    with pytest.raises(ValueError, match="line 8: after 2 names this story or a later one"):
        stub.parse_split(text)


def test_parse_split_refuses_an_after_position_named_twice():
    text = _split(BULLETS[0], BULLETS[1], "- Admin view | Show grants. (after 1, 1)")

    with pytest.raises(ValueError, match="line 9: after 1 is named twice"):
        stub.parse_split(text)


def test_parse_split_refuses_a_file_without_the_requirements_heading():
    with pytest.raises(ValueError, match="line 1: expected ## Requirements"):
        stub.parse_split(f"# Requirements\n\n{REQUIREMENTS}\n\n## Stories\n\n{BULLETS[0]}\n")


def test_parse_split_refuses_requirements_with_no_text():
    with pytest.raises(ValueError, match="line 1: ## Requirements has no text"):
        stub.parse_split(_split(*BULLETS, requirements=""))


def test_parse_split_refuses_a_file_without_the_stories_heading():
    with pytest.raises(ValueError, match="expected ## Stories"):
        stub.parse_split(f"## Requirements\n\n{REQUIREMENTS}\n")


def test_parse_split_refuses_a_stories_list_with_no_bullets():
    with pytest.raises(ValueError, match="line 5: ## Stories lists no stories"):
        stub.parse_split(_split())


# --- render and read --------------------------------------------------------


def test_render_numbers_the_entries_and_carries_their_issue_numbers():
    assert stub.render(REQUIREMENTS, NUMBERED) == (
        "## Requirements\n"
        "\n"
        f"{REQUIREMENTS}\n"
        "\n"
        "## Stories\n"
        "\n"
        "1. #57 Grant store | Persist grants.\n"
        "2. #58 Handler filter | Filter by grant. (after 1)\n"
        "3. #59 Admin view | Show grants. (after 1, 2)\n"
    )


def test_render_leaves_out_the_issue_number_of_an_entry_that_has_none():
    body = stub.render(REQUIREMENTS, ENTRIES)

    assert body.splitlines()[-3:] == [
        "1. Grant store | Persist grants.",
        "2. Handler filter | Filter by grant. (after 1)",
        "3. Admin view | Show grants. (after 1, 2)",
    ]


def test_render_of_no_entries_ends_at_the_stories_heading():
    assert stub.render(REQUIREMENTS, []) == f"## Requirements\n\n{REQUIREMENTS}\n\n## Stories\n"


def test_read_is_the_inverse_of_render():
    assert stub.read(stub.render(REQUIREMENTS, NUMBERED)) == (REQUIREMENTS, NUMBERED)
    assert stub.read(stub.render(REQUIREMENTS, ENTRIES)) == (REQUIREMENTS, ENTRIES)
    assert stub.read(stub.render(REQUIREMENTS, [])) == (REQUIREMENTS, [])


def test_a_title_that_carries_a_colon_survives_the_round_trip():
    entries = [Entry("Store: grants", "Persist grants.", (), 57)]

    requirements, read = stub.read(stub.render(REQUIREMENTS, entries))

    assert (requirements, read) == (REQUIREMENTS, entries)


def test_parse_split_keeps_a_colon_inside_a_title():
    requirements, entries = stub.parse_split(_split("- Store: grants | Persist grants."))

    assert (requirements, entries) == (REQUIREMENTS, [Entry("Store: grants", "Persist grants.", ())])


def test_read_tolerates_carriage_returns():
    assert stub.read(stub.render(REQUIREMENTS, NUMBERED).replace("\n", "\r\n")) == (REQUIREMENTS, NUMBERED)


def test_read_of_the_parked_fixture_finds_its_requirements_and_no_entries():
    parked = json.loads((FIXTURES / "stub-parked.json").read_text(encoding="utf-8"))

    requirements, entries = stub.read(parked["body"])

    assert entries == []
    assert requirements.startswith("Guests should be able to share")


def test_read_of_the_stub_fixture_finds_its_three_numbered_entries():
    data = json.loads((FIXTURES / "stub.json").read_text(encoding="utf-8"))

    assert stub.read(data["body"]) == (REQUIREMENTS, NUMBERED)


# --- is_stub ----------------------------------------------------------------


def test_is_stub_is_false_for_a_story_body():
    assert stub.is_stub((FIXTURES / "body-valid.md").read_text(encoding="utf-8")) is False


def test_is_stub_is_true_for_a_rendered_stub():
    assert stub.is_stub(stub.render(REQUIREMENTS, NUMBERED)) is True


def test_is_stub_is_true_for_a_parked_feature():
    assert stub.is_stub(stub.render(REQUIREMENTS, [])) is True


def test_is_stub_is_false_for_an_empty_body():
    assert stub.is_stub("") is False


def test_is_stub_looks_past_leading_blank_lines():
    assert stub.is_stub(f"\n\n  ## Requirements\n\n{REQUIREMENTS}\n") is True


def test_is_stub_is_false_for_another_heading():
    assert stub.is_stub("## Review\n\nNothing found.\n") is False


# --- lists_stories -----------------------------------------------------------


def test_lists_stories_is_true_for_the_bullets_a_session_writes():
    assert stub.lists_stories(_split(*BULLETS)) is True


def test_lists_stories_is_true_for_the_numbered_list_render_writes():
    assert stub.lists_stories(stub.render(REQUIREMENTS, NUMBERED)) is True


def test_lists_stories_is_false_for_a_parked_feature():
    assert stub.lists_stories(stub.render(REQUIREMENTS, [])) is False


def test_lists_stories_is_false_without_the_stories_heading():
    assert stub.lists_stories(f"## Requirements\n\n{REQUIREMENTS}\n") is False


def test_lists_stories_ignores_a_bullet_above_the_stories_heading():
    assert stub.lists_stories(_split(requirements=f"{REQUIREMENTS}\n\n- One requirement")) is False
