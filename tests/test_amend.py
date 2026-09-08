from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from deckhand import sections
from tests.conftest import FIXTURES, run_deckhand

STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
ISSUE = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
REVIEW = json.loads((FIXTURES / "issue-reviewed.json").read_text(encoding="utf-8"))
BODY = ISSUE["body"]
NOTES = sections.get(BODY, "Notes")
REVIEWED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-reviewed.json")}
NOTE = "the grant lookup retries once when the store times out"
NEXT = "Next: continue where execution stopped, or reply Approved and /deckhand:ready 248."
TODAY = datetime.datetime.now(datetime.UTC).date().isoformat()
TITLE = "Index the grant table on principal"


def _draft(tmp_path: Path, text: str = BODY) -> str:
    path = tmp_path / "248-body.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _written(copy: Path) -> str:
    """The body `gh issue edit` was handed, without the fake's `--- issue edit` marker line."""
    text = copy.read_text(encoding="utf-8")
    marker = "--- issue edit\n"
    assert text.startswith(marker)
    return text[len(marker) :]


def _bodies(copy: Path) -> dict[str, str]:
    """Every body file the fake recorded, by the `<verb>` of its marker line."""
    bodies: dict[str, str] = {}
    for chunk in copy.read_text(encoding="utf-8").split("--- issue ")[1:]:
        verb, _, text = chunk.partition("\n")
        bodies[verb] = text
    return bodies


def _review(tmp_path: Path, name: str, body: str) -> dict[str, str]:
    """An env pointing the fake gh at the reviewed issue with `body` as its review comment."""
    data = json.loads(json.dumps(REVIEW))
    data["comments"][-1]["body"] = body
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


def _writes(gh_calls) -> list[str]:
    """The recorded calls that write, with the temp body-file path cut off."""
    starts = ("issue create", "issue edit", "issue comment", "api -X POST")
    return [call.split(" --body-file")[0] for call in gh_calls() if call.startswith(starts)]


def _wrapper(tmp_path: Path, script: str) -> dict[str, str]:
    """A gh on PATH ahead of the fake that runs `script` (bash) and can fall through with `exec`."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    (fake_bin / "gh").write_text(script)
    (fake_bin / "gh").chmod(0o755)
    return {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}


# --- context ----------------------------------------------------------------


def test_context_prints_body_review_and_draft_path(fake_gh, tmp_path):
    result = run_deckhand("amend", "context", "248", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "### Story"
    assert lines[lines.index("## Latest review") - 1] == ""
    review = REVIEW["comments"][-1]["body"].strip("\n").splitlines()
    assert lines[lines.index("## Latest review") + 1 :][: len(review)] == review
    assert lines[-2] == f"Draft: {tmp_path / 'cache' / 'widgets' / '248-body.md'}"
    assert lines[-1] == "Write the whole edited body to the draft; keep every section heading."


def test_context_prints_none_without_a_review(fake_gh):
    result = run_deckhand("amend", "context", "248")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("## Latest review") + 1] == "  none"


def test_context_never_fails(fake_gh, tmp_path):
    env = _wrapper(tmp_path, "#!/usr/bin/env bash\necho nope >&2\nexit 1\n")

    result = run_deckhand("amend", "context", "248", env=env)

    assert result.returncode == 0
    assert result.stderr == ""
    out = result.stdout
    assert "Body: unavailable (nope)" in out
    assert "## Latest review\n  unavailable (nope)" in out
    assert "Draft: unavailable (nope)" in out


# --- apply, the story's own body ---------------------------------------------


def test_apply_refuses_a_stub(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("amend", "apply", "57", _draft(tmp_path), "--note", NOTE, env=STUB)

    assert result.returncode == 1
    assert result.stderr == "deckhand amend apply: #57 is a stub; run /deckhand:new 57 first\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_refuses_changed_headings(fake_gh, gh_calls, tmp_path):
    preamble, parsed = sections.parse(BODY)
    without_notes = sections.render(preamble, [pair for pair in parsed if pair[0] != "Notes"])

    result = run_deckhand("amend", "apply", "248", _draft(tmp_path, without_notes), "--note", NOTE)

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand amend apply: headings changed: "
        "expected Story, Scope, Acceptance Criteria, Plan, Notes; "
        "got Story, Scope, Acceptance Criteria, Plan\n"
    )
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_refuses_when_the_result_fails_lint(fake_gh, gh_calls, tmp_path):
    broken = sections.replace(BODY, "Story", "Guests should see fewer collections.")

    result = run_deckhand("amend", "apply", "248", _draft(tmp_path, broken), "--note", NOTE)

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand amend apply: body: ")
    assert "Story is not one" in result.stderr
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_appends_the_amended_line(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "body-copy.md"

    result = run_deckhand(
        "amend", "apply", "248", _draft(tmp_path), "--note", NOTE, env={"GH_BODY_FILE_COPY": str(copy)}
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Updated #248 https://github.com/acme/widgets/issues/248",
        NEXT,
    ]
    written = _written(copy)
    assert sections.get(written, "Notes") == f"{NOTES}\n- Amended {TODAY}: {NOTE}"
    assert written == sections.replace(BODY, "Notes", f"{NOTES}\n- Amended {TODAY}: {NOTE}")
    assert _writes(gh_calls) == ["issue edit 248 --repo acme/widgets"]


def test_apply_records_accepted_and_rejected_from_the_ticks(fake_gh, tmp_path):
    copy = tmp_path / "body-copy.md"

    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path),
        "--note",
        NOTE,
        env={**REVIEWED, "GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert sections.get(_written(copy), "Notes") == (
        f"{NOTES}\n- Amended {TODAY}: {NOTE}\n- Accepted chaos.1\n- Rejected unknowns.1"
    )


def test_apply_does_not_record_a_tick_twice(fake_gh, tmp_path):
    copy = tmp_path / "body-copy.md"
    already = sections.replace(BODY, "Notes", f"{NOTES}\n- Accepted chaos.1")

    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path, already),
        "--note",
        NOTE,
        env={**REVIEWED, "GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    notes = sections.get(_written(copy), "Notes")
    assert notes.count("- Accepted chaos.1") == 1
    assert notes == f"{NOTES}\n- Accepted chaos.1\n- Amended {TODAY}: {NOTE}\n- Rejected unknowns.1"


# --- apply, a story of its own ------------------------------------------------


def test_new_issue_creates_links_and_comments(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "body-copy.md"
    body = (FIXTURES / "body-valid.md").read_text(encoding="utf-8")

    result = run_deckhand(
        "amend", "apply", "248", _draft(tmp_path, body), "--new-issue", TITLE, env={"GH_BODY_FILE_COPY": str(copy)}
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Created #999 https://github.com/acme/widgets/issues/999",
        "Blocked by #248",
        "Commented on #248",
        NEXT,
    ]
    assert _writes(gh_calls) == [
        f"issue create --repo acme/widgets --title {TITLE}",
        "api -X POST repos/acme/widgets/issues/999/dependencies/blocked_by -F issue_id=5099965156",
        "issue comment 248 --repo acme/widgets",
    ]
    bodies = _bodies(copy)
    assert bodies["create"] == body
    assert bodies["comment"] == f"Split: #999 {TITLE}, blocked by this story."


def test_new_issue_refuses_a_bad_body(fake_gh, gh_calls, tmp_path):
    body = (FIXTURES / "body-invalid.md").read_text(encoding="utf-8")

    result = run_deckhand("amend", "apply", "248", _draft(tmp_path, body), "--new-issue", TITLE)

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand amend apply: body: ")
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_note_and_new_issue_are_exclusive(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("amend", "apply", "248", _draft(tmp_path), "--note", NOTE, "--new-issue", TITLE)

    assert result.returncode == 2
    assert "not allowed with" in result.stderr
    assert _writes(gh_calls) == []


def test_apply_needs_one_of_the_two_modes(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("amend", "apply", "248", _draft(tmp_path))

    assert result.returncode == 2
    assert "one of the arguments" in result.stderr
    assert _writes(gh_calls) == []


def test_apply_refuses_an_unreadable_draft(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("amend", "apply", "248", str(tmp_path / "nope.md"), "--note", NOTE)

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand amend apply: cannot read ")
    assert _writes(gh_calls) == []


def test_the_step_is_registered_with_both_verbs(fake_gh):
    result = run_deckhand("amend", "--help")

    assert result.returncode == 0
    assert "{context,apply}" in result.stdout


# --- decisions ----------------------------------------------------------------


def test_decisions_wait_for_the_first_tick(fake_gh, tmp_path):
    untriaged = REVIEW["comments"][-1]["body"].replace("- [x]", "- [ ]")
    first_copy = tmp_path / "first.md"

    first = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path),
        "--note",
        NOTE,
        env={**_review(tmp_path, "untriaged.json", untriaged), "GH_BODY_FILE_COPY": str(first_copy)},
    )

    assert first.returncode == 0, first.stderr
    assert sections.get(_written(first_copy), "Notes") == f"{NOTES}\n- Amended {TODAY}: {NOTE}"

    second_copy = tmp_path / "second.md"
    second = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path, _written(first_copy)),
        "--note",
        "the retry landed",
        env={**REVIEWED, "GH_BODY_FILE_COPY": str(second_copy)},
    )

    assert second.returncode == 0, second.stderr
    notes = sections.get(_written(second_copy), "Notes")
    assert notes.count("- Accepted chaos.1") == 1
    assert "- Rejected chaos.1" not in notes


def test_a_flipped_verdict_rewrites_its_line(fake_gh, tmp_path):
    copy = tmp_path / "body-copy.md"
    already = sections.replace(BODY, "Notes", f"{NOTES}\n- Rejected unknowns.1 confirm the index")
    ticked = REVIEW["comments"][-1]["body"].replace("- [ ] unknowns.1", "- [x] unknowns.1")

    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path, already),
        "--note",
        NOTE,
        env={**_review(tmp_path, "both-ticked.json", ticked), "GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert sections.get(_written(copy), "Notes") == (
        f"{NOTES}\n- Accepted unknowns.1 confirm the index\n- Amended {TODAY}: {NOTE}\n- Accepted chaos.1"
    )


def test_boxes_count_with_any_bullet_and_a_capital_tick(fake_gh, tmp_path):
    copy = tmp_path / "body-copy.md"
    review = "## Review\n\n### Decisions\n\n* [X] chaos.1\n+ [ ] unknowns.1\n"

    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path),
        "--note",
        NOTE,
        env={**_review(tmp_path, "bullets.json", review), "GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert sections.get(_written(copy), "Notes") == (
        f"{NOTES}\n- Amended {TODAY}: {NOTE}\n- Accepted chaos.1\n- Rejected unknowns.1"
    )


def test_apply_collapses_whitespace_in_the_note(fake_gh, tmp_path):
    copy = tmp_path / "body-copy.md"

    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path),
        "--note",
        "the grant lookup\n  retries once",
        env={"GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    assert sections.get(_written(copy), "Notes") == f"{NOTES}\n- Amended {TODAY}: the grant lookup retries once"


def test_apply_refuses_a_blank_note(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("amend", "apply", "248", _draft(tmp_path), "--note", "   ")

    assert result.returncode == 1
    assert result.stderr == "deckhand amend apply: --note needs a line saying what changed and why\n"
    assert _writes(gh_calls) == []


# --- a story of its own -------------------------------------------------------


def test_new_issue_names_the_created_issue_before_the_dependency_write(fake_gh, gh_calls, tmp_path):
    body = (FIXTURES / "body-valid.md").read_text(encoding="utf-8")

    result = run_deckhand(
        "amend", "apply", "248", _draft(tmp_path, body), "--new-issue", TITLE, env={"GH_DEPENDENCY_FAILS": "1"}
    )

    assert result.returncode == 1
    # Flushed as it is printed: the number has to survive whatever goes wrong after it.
    assert result.stdout.splitlines() == ["Created #999 https://github.com/acme/widgets/issues/999"]
    assert "could not add dependency" in result.stderr
    assert "issue comment 248 --repo acme/widgets" not in _writes(gh_calls)


def test_new_issue_refuses_a_blank_title(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("amend", "apply", "248", _draft(tmp_path), "--new-issue", "  ")

    assert result.returncode == 1
    assert result.stderr == "deckhand amend apply: --new-issue needs a title\n"
    assert _writes(gh_calls) == []
