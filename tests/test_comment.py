"""The plain command: one comment the process writes, posted as it was typed."""

from __future__ import annotations

from tests.conftest import run_deckhand

TEXT = "Deviation: the retry path moved into the store, so the handler stays thin."
URL = "https://github.com/acme/widgets/issues/248#issuecomment-77"


def _comments(gh_calls) -> list[str]:
    """The recorded comment calls, with the temp body-file path cut off."""
    return [call.split(" --body-file")[0] for call in gh_calls() if call.startswith("issue comment")]


def _posted(copy) -> str:
    """The body `gh issue comment` was handed, without the fake's marker line."""
    text = copy.read_text(encoding="utf-8")
    marker = "--- issue comment\n"
    assert text.startswith(marker)
    return text[len(marker) :]


def test_comment_posts_the_text_and_prints_the_url(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "body-copy.md"

    result = run_deckhand("comment", "248", TEXT, env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{URL}\n"
    assert _posted(copy) == TEXT
    assert _comments(gh_calls) == ["issue comment 248 --repo acme/widgets"]


def test_the_text_is_posted_verbatim(fake_gh, tmp_path):
    """The process prefixes nothing: what the session typed is what the issue carries."""
    copy = tmp_path / "body-copy.md"
    text = "Deviation: two things moved.\n\n- the retry path\n- the grant lookup\n"

    result = run_deckhand("comment", "248", text, env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert _posted(copy) == text


def test_blank_text_is_a_usage_error(fake_gh, gh_calls):
    result = run_deckhand("comment", "248", "   ")

    assert result.returncode == 2
    assert "cannot be blank" in result.stderr
    assert _comments(gh_calls) == []


def test_a_non_numeric_issue_is_a_usage_error(fake_gh, gh_calls):
    result = run_deckhand("comment", "two-four-eight", TEXT)

    assert result.returncode == 2
    assert "issue number must be an integer" in result.stderr
    assert _comments(gh_calls) == []


def test_a_failed_post_is_one_line_on_stderr(fake_gh):
    result = run_deckhand("comment", "248", TEXT, env={"GH_ISSUE_COMMENT_FAILS": "1"})

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("deckhand comment: ")


def test_the_command_takes_no_verb(fake_gh):
    """It writes one comment and nothing else, so it has no context to print and no apply to run."""
    result = run_deckhand("comment", "--help")

    assert result.returncode == 0
    assert "{context" not in result.stdout
