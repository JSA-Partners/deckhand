from __future__ import annotations

import pytest

from deckhand import naming
from tests.conftest import FIXTURES

VALID = FIXTURES / "body-valid.md"
STORY_TITLE = "See only the collections I was granted"
WANT = "to see only the collections I was granted"


def _want(clause: str) -> str:
    """The valid body with the Story's I want clause replaced by `clause`."""
    return VALID.read_text(encoding="utf-8").replace(WANT, clause)


# --- slug ---------------------------------------------------------------


def test_slug_lowercase_hyphenate_first_four_words():
    assert naming.slug("Guest users see only their granted collections") == "guest-users-see-only"


def test_slug_collapses_punctuation_and_trims():
    assert naming.slug("  Hello,  World!  ") == "hello-world"


def test_slug_keeps_short_titles_whole():
    assert naming.slug("Fix login") == "fix-login"


def test_slug_rejects_a_title_with_no_usable_characters():
    with pytest.raises(ValueError, match="no usable characters"):
        naming.slug("!!!")


# --- branch-name ----------------------------------------------------------


def test_branch_name_renders_kind_number_slug(settings):
    result = naming.branch_name(settings, "feat", 248, "Guest users see only their granted collections")
    assert result == "feat/248-guest-users-see-only"


def test_branch_name_rejects_an_unknown_kind(settings):
    with pytest.raises(ValueError, match="unknown kind"):
        naming.branch_name(settings, "feature", 248, "x")


def test_branch_name_rejects_a_non_numeric_issue(settings):
    with pytest.raises(ValueError):
        naming.branch_name(settings, "feat", "abc", "x")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Café niño login", "feat/248-caf-ni-o-login"),
        ("248 Fix Login", "feat/248-248-fix-login"),
        ("Add login\nand logout", "feat/248-add-login-and-logout"),
        ("UPPER Case And Punctuation!!", "feat/248-upper-case-and-punctuation"),
    ],
)
def test_branch_name_is_a_valid_git_ref_for_awkward_titles(settings, title, expected):
    assert naming.branch_name(settings, "feat", 248, title) == expected


def test_branch_name_refuses_an_empty_slug(settings):
    with pytest.raises(ValueError, match="no usable slug"):
        naming.branch_name(settings, "feat", 248, "🚀 ✨")


def test_branch_name_rejects_a_two_word_kind(settings):
    with pytest.raises(ValueError):
        naming.branch_name(settings, "feat fix", 248, "x")


# --- pr-title ---------------------------------------------------------------


def test_pr_title_renders_kind_title(settings):
    result = naming.pr_title(settings, "feat", "Guest users see only their granted collections")
    assert result == "feat: Guest users see only their granted collections"


def test_pr_title_adds_bang_when_breaking(settings):
    result = naming.pr_title(settings, "fix", "Drop the v0 routes", breaking=True)
    assert result == "fix!: Drop the v0 routes"


def test_pr_title_rejects_an_unknown_kind(settings):
    with pytest.raises(ValueError, match="unknown kind"):
        naming.pr_title(settings, "feature", "x")


def test_pr_title_refuses_a_subject_over_72(settings):
    with pytest.raises(ValueError, match="title too long for a commit subject; shorten the issue title"):
        naming.pr_title(settings, "feat", "x" * 67)


def test_pr_title_returns_a_subject_of_exactly_72(settings):
    result = naming.pr_title(settings, "feat", "x" * 65, breaking=True)
    assert result == f"feat!: {'x' * 65}"
    assert len(result) == 72


def test_pr_title_reserves_the_bang_it_may_not_use(settings):
    """A title shown without the bang must still fit once a breaking change adds one."""
    assert naming.pr_title(settings, "feat", "x" * 65) == f"feat: {'x' * 65}"
    with pytest.raises(ValueError, match="title too long"):
        naming.pr_title(settings, "feat", "x" * 66)


# --- title ------------------------------------------------------------------


def test_title_comes_from_the_story_or_the_flag():
    body = VALID.read_text(encoding="utf-8")

    assert naming.title(None, body) == STORY_TITLE
    assert naming.title("  Guest filtering  ", body) == "Guest filtering"


def test_a_derived_title_drops_the_clauses_leading_to():
    assert naming.title(None, _want("to hand one command both versions")) == "Hand one command both versions"
    assert naming.title(None, _want("total control of the version")) == "Total control of the version"


def test_title_rejects_a_body_with_no_i_want_clause():
    with pytest.raises(ValueError, match="no title"):
        naming.title(None, "### Story\n\nGuests should see fewer collections.\n")
    with pytest.raises(ValueError, match="no title"):
        naming.title("   ", "### Notes\n\n- none\n")


def test_the_title_limit_is_what_the_pull_request_subject_leaves(settings):
    limit = naming.title_limit()
    longest = max(settings.kinds, key=len)

    assert limit == naming.SUBJECT - len(longest) - len(f"{naming.BANG}: ")
    assert naming.pr_title(settings, longest, "x" * limit, breaking=True)
    with pytest.raises(ValueError, match="too long"):
        naming.pr_title(settings, longest, "x" * (limit + 1))


def test_fits_takes_a_title_of_exactly_the_limit_and_rejects_one_over():
    limit = naming.title_limit()

    assert naming.fits("x" * limit) == "x" * limit
    with pytest.raises(ValueError, match=f"title is {limit + 1} characters; the pull request subject allows"):
        naming.fits("x" * (limit + 1))


# --- pr-body ------------------------------------------------------------

STORY = "As a guest user, I want to see only the collections I was granted."


def test_pr_body_renders_the_story_and_the_closes_footer():
    assert naming.pr_body(248, STORY) == f"{STORY}\n\nCloses #248\n"


def test_pr_body_adds_the_breaking_footer():
    result = naming.pr_body(248, STORY, breaking="Drops /v0")
    assert result == f"{STORY}\n\nBREAKING CHANGE: Drops /v0\nCloses #248\n"


def test_pr_body_rewraps_a_story_written_over_several_lines():
    story = (
        "As a guest user,\nI want to see only  the collections I was granted,\n"
        "so that I am not exposed to other organizations' data."
    )

    paragraph = naming.pr_body(248, story).split("\n\nCloses")[0]

    assert paragraph == (
        "As a guest user, I want to see only the collections I was granted, so"
        "\nthat I am not exposed to other organizations' data."
    )


def test_pr_body_wraps_at_72():
    assert naming.WRAP == 72

    story = " ".join(f"word{i}" for i in range(30))
    paragraph = naming.pr_body(248, story).split("\n\nCloses")[0]

    assert all(len(line) <= 72 for line in paragraph.splitlines())


def test_pr_body_keeps_a_73_character_word_whole():
    word = "x" * 73

    paragraph = naming.pr_body(248, f"See {word} here.").split("\n\nCloses")[0]

    assert word in paragraph.splitlines()


def test_pr_body_never_splits_a_long_token():
    url = "https://example.test/docs/very/long/path/to/the/grants/documentation/page#the-guest-collections-filter"
    assert len(url) > 100, "the case is a token the wrap cannot fit on a line at all"

    paragraph = naming.pr_body(248, f"The rule a guest is filtered by is written up at {url} today.")

    assert url in paragraph.split("\n\nCloses")[0].splitlines()


def test_pr_body_rejects_a_non_numeric_issue():
    with pytest.raises(ValueError):
        naming.pr_body("abc", STORY)
