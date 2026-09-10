"""The issue's log: prefixed comments the process writes, read back in order."""

from __future__ import annotations

from deckhand import issue, log
from tests.conftest import FIXTURES, run_deckhand


def _story(*bodies: str) -> issue.Issue:
    comments = [
        issue.Comment(author="claude", body=body, created_at=f"2026-09-0{i + 1}T10:00:00Z")
        for i, body in enumerate(bodies)
    ]
    return issue.Issue(
        number=248,
        title="T",
        body="",
        url="https://github.com/acme/widgets/issues/248",
        state="OPEN",
        comments=comments,
    )


def test_entries_reads_only_prefixed_comments_in_order():
    story = _story(
        "Drafted: the story", "hello from a person", "Review: sound\n\n- chaos.1 accepted", "Amended: a note"
    )

    found = log.entries(story)

    assert [(e.prefix, e.text) for e in found] == [
        ("Drafted:", "the story"),
        ("Review:", "sound"),
        ("Amended:", "a note"),
    ]
    assert found[1].body.startswith("Review: sound\n")
    assert found[1].created_at == "2026-09-03T10:00:00Z"


def test_a_prefix_has_to_open_the_comment_and_be_followed_by_text():
    story = _story(
        "  Amended: spaced in",
        "Amended:",
        "Not Amended: this",
        "amended: lowercase",
        "\n\nAmended: after a blank line",
    )

    assert [(e.prefix, e.text) for e in log.entries(story)] == [
        ("Amended:", "spaced in"),
        ("Amended:", "after a blank line"),
    ]


def test_last_is_the_latest_entry_with_that_prefix():
    story = _story("Amended: one", "Review: two", "Amended: three")

    assert log.last(story, "Amended:").text == "three"
    assert log.last(story, "Started:") is None


def test_the_review_fixture_reads_as_a_review(fake_gh, monkeypatch):
    """The fixture every reviewed-story test points at carries a `Review:` entry the log reads back."""
    monkeypatch.setenv("GH_ISSUE_FILE", str(FIXTURES / "issue-reviewed.json"))

    assert log.last(issue.view("acme/widgets", 248), "Review:") is not None


def test_since_is_every_entry_after_the_latest_with_that_prefix():
    story = _story("Review: r1", "Amended: a1", "Review: r2", "Amended: a2", "Deviation: d1")

    assert [e.text for e in log.since(story, "Review:")] == ["a2", "d1"]
    assert [e.text for e in log.since(story, "Started:")] == ["r1", "a1", "r2", "a2", "d1"]
    assert log.since(story, "Deviation:") == []


def test_the_command_posts_the_text_as_written(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "comment.md"

    result = run_deckhand(
        "log", "248", "Deviation: the flags, on lines this branch touches", env={"GH_BODY_FILE_COPY": str(copy)}
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "https://github.com/acme/widgets/issues/248#issuecomment-77\n"
    assert copy.read_text(encoding="utf-8") == "--- issue comment\nDeviation: the flags, on lines this branch touches"
    assert [c.split(" --body-file")[0] for c in gh_calls() if c.startswith("issue comment")] == [
        "issue comment 248 --repo acme/widgets"
    ]


def test_the_command_refuses_text_with_no_prefix(fake_gh, gh_calls):
    result = run_deckhand("log", "248", "just a note")

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand log: the text must open with one of: Drafted:, Review:, Amended:, Started:, Deviation:, Split:, "
        "Reviewed:, Pull request:, After the merge:\n"
    )
    assert [c for c in gh_calls() if c.startswith("issue comment")] == []


def test_the_command_refuses_a_prefix_with_nothing_after_it(fake_gh, gh_calls):
    result = run_deckhand("log", "248", "Deviation:   ")

    assert result.returncode == 1
    assert "nothing after" in result.stderr
    assert [c for c in gh_calls() if c.startswith("issue comment")] == []


def test_the_command_refuses_a_prefix_whose_text_is_on_a_later_line(fake_gh, gh_calls):
    """The command checks the first line the log will read, so it never posts what `entries` skips."""
    result = run_deckhand("log", "248", "Deviation:\n\nlater")

    assert result.returncode == 1
    assert "nothing after" in result.stderr
    assert [c for c in gh_calls() if c.startswith("issue comment")] == []
