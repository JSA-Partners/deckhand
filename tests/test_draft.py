from __future__ import annotations

import json

import pytest

from deckhand import draft
from deckhand.draft import Entry
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


def _doc(stories: list[dict], requirements: str = REQUIREMENTS) -> str:
    return json.dumps({"kind": "split", "requirements": requirements, "stories": stories})


STORIES = [
    {"key": "grant-store", "title": "Grant store", "sentence": "Persist grants."},
    {"key": "handler-filter", "title": "Handler filter", "sentence": "Filter by grant.", "after": ["grant-store"]},
    {
        "key": "admin-view",
        "title": "Admin view",
        "sentence": "Show grants.",
        "after": ["grant-store", "handler-filter"],
    },
]


def test_parse_split_reads_the_requirements_and_resolves_every_key():
    """The keys are the authored form; what is stored is the position, which deckhand generates."""
    requirements, entries = draft.parse_split(_doc(STORIES))

    assert requirements == REQUIREMENTS
    assert entries == ENTRIES


def test_parse_split_refuses_an_after_that_names_a_later_story():
    stories = [STORIES[0], {**STORIES[1], "after": ["admin-view"]}, STORIES[2]]

    with pytest.raises(ValueError, match="story 2: after names 'admin-view', which is not a story defined before it"):
        draft.parse_split(_doc(stories))


def test_parse_split_refuses_an_after_that_names_nothing():
    stories = [STORIES[0], {**STORIES[1], "after": ["nowhere"]}]

    with pytest.raises(ValueError, match="story 2: after names 'nowhere'"):
        draft.parse_split(_doc(stories))


def test_parse_split_refuses_an_after_that_names_itself():
    stories = [STORIES[0], {**STORIES[1], "after": ["handler-filter"]}]

    with pytest.raises(ValueError, match="story 2: after names 'handler-filter'"):
        draft.parse_split(_doc(stories))


def test_parse_split_refuses_a_key_named_twice_in_one_after():
    stories = [STORIES[0], {**STORIES[1], "after": ["grant-store", "grant-store"]}]

    with pytest.raises(ValueError, match="story 2: after names 'grant-store' twice"):
        draft.parse_split(_doc(stories))


def test_parse_split_refuses_two_stories_with_one_key():
    stories = [STORIES[0], {**STORIES[1], "key": "grant-store"}]

    with pytest.raises(ValueError, match="story 2: 'grant-store' is already the key of an earlier story"):
        draft.parse_split(_doc(stories))


@pytest.mark.parametrize("field", ["key", "title", "sentence"])
def test_parse_split_refuses_a_story_missing_a_field(field):
    story = {key: value for key, value in STORIES[0].items() if key != field}

    with pytest.raises(ValueError, match=f"story 1: needs a {field}"):
        draft.parse_split(_doc([story]))


def test_parse_split_refuses_requirements_with_no_text():
    with pytest.raises(ValueError, match="'requirements' says what the feature needs"):
        draft.parse_split(_doc(STORIES, requirements="  "))


def test_parse_split_refuses_a_document_with_no_stories():
    with pytest.raises(ValueError, match="'stories' lists one story per issue"):
        draft.parse_split(_doc([]))


def test_parse_split_refuses_a_document_that_is_not_a_split():
    with pytest.raises(ValueError, match='opens with {"kind": "split"'):
        draft.parse_split(json.dumps({"kind": "findings", "findings": []}))


def test_parse_split_refuses_text_that_is_not_json():
    with pytest.raises(ValueError, match="line 1 column 1"):
        draft.parse_split("## Requirements\n\n- Grant store | Persist grants.\n")


def test_parse_split_keeps_a_colon_inside_a_title():
    """A colon in a title cost a mistitled issue under the old grammar; JSON cannot lose it."""
    story = {"key": "store", "title": "Store: grants", "sentence": "Persist grants."}

    _, entries = draft.parse_split(_doc([story]))

    assert entries == [Entry("Store: grants", "Persist grants.", ())]


def test_parse_split_reads_a_repository():
    stories = [
        {"key": "endpoint", "title": "Endpoint", "sentence": "Lists grants.", "repo": "acme/gadgets"},
        {"key": "screen", "title": "Screen", "sentence": "Shows them.", "after": ["endpoint"]},
    ]

    _, entries = draft.parse_split(_doc(stories, requirements="R."))

    assert [(e.repo, e.title, e.after) for e in entries] == [("acme/gadgets", "Endpoint", ()), (None, "Screen", (1,))]


# --- render and read --------------------------------------------------------


def test_render_numbers_the_entries_and_carries_their_issue_numbers():
    assert draft.render(REQUIREMENTS, NUMBERED) == (
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
    body = draft.render(REQUIREMENTS, ENTRIES)

    assert body.splitlines()[-3:] == [
        "1. Grant store | Persist grants.",
        "2. Handler filter | Filter by grant. (after 1)",
        "3. Admin view | Show grants. (after 1, 2)",
    ]


def test_render_of_no_entries_ends_at_the_stories_heading():
    assert draft.render(REQUIREMENTS, []) == f"## Requirements\n\n{REQUIREMENTS}\n\n## Stories\n"


def test_read_is_the_inverse_of_render():
    assert draft.read(draft.render(REQUIREMENTS, NUMBERED)) == (REQUIREMENTS, NUMBERED)
    assert draft.read(draft.render(REQUIREMENTS, ENTRIES)) == (REQUIREMENTS, ENTRIES)
    assert draft.read(draft.render(REQUIREMENTS, [])) == (REQUIREMENTS, [])


def test_a_title_that_carries_a_colon_survives_the_round_trip():
    entries = [Entry("Store: grants", "Persist grants.", (), 57)]

    requirements, read = draft.read(draft.render(REQUIREMENTS, entries))

    assert (requirements, read) == (REQUIREMENTS, entries)


def test_read_tolerates_carriage_returns():
    assert draft.read(draft.render(REQUIREMENTS, NUMBERED).replace("\n", "\r\n")) == (REQUIREMENTS, NUMBERED)


def test_read_of_the_parked_fixture_finds_its_requirements_and_no_entries():
    parked = json.loads((FIXTURES / "draft-parked.json").read_text(encoding="utf-8"))

    requirements, entries = draft.read(parked["body"])

    assert entries == []
    assert requirements.startswith("Guests should be able to share")


def test_read_of_the_stub_fixture_finds_its_three_numbered_entries():
    data = json.loads((FIXTURES / "draft.json").read_text(encoding="utf-8"))

    assert draft.read(data["body"]) == (REQUIREMENTS, NUMBERED)


# --- is_draft ----------------------------------------------------------------


def test_is_stub_is_false_for_a_story_body():
    assert draft.is_draft((FIXTURES / "body-valid.md").read_text(encoding="utf-8")) is False


def test_is_stub_is_true_for_a_rendered_stub():
    assert draft.is_draft(draft.render(REQUIREMENTS, NUMBERED)) is True


def test_is_stub_is_true_for_a_parked_feature():
    assert draft.is_draft(draft.render(REQUIREMENTS, [])) is True


def test_is_stub_is_false_for_an_empty_body():
    assert draft.is_draft("") is False


def test_is_stub_looks_past_leading_blank_lines():
    assert draft.is_draft(f"\n\n  ## Requirements\n\n{REQUIREMENTS}\n") is True


def test_is_stub_is_false_for_another_heading():
    assert draft.is_draft("## Review\n\nNothing found.\n") is False


# --- lists_stories -----------------------------------------------------------


def test_lists_stories_is_true_for_the_bullets_a_session_writes():
    assert draft.lists_stories(_split(*BULLETS)) is True


def test_lists_stories_is_true_for_the_numbered_list_render_writes():
    assert draft.lists_stories(draft.render(REQUIREMENTS, NUMBERED)) is True


def test_lists_stories_is_false_for_a_parked_feature():
    assert draft.lists_stories(draft.render(REQUIREMENTS, [])) is False


def test_lists_stories_is_false_without_the_stories_heading():
    assert draft.lists_stories(f"## Requirements\n\n{REQUIREMENTS}\n") is False


def test_lists_stories_ignores_a_bullet_above_the_stories_heading():
    assert draft.lists_stories(_split(requirements=f"{REQUIREMENTS}\n\n- One requirement")) is False


def test_render_writes_the_repository_prefix_back():
    entry = Entry(title="Endpoint", sentence="Lists grants.", after=(), number=60, repo="acme/gadgets")

    body = draft.render("R.", [entry])

    assert "1. acme/gadgets: #60 Endpoint | Lists grants." in body
    assert draft.read(body)[1][0].repo == "acme/gadgets"
