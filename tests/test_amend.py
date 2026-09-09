from __future__ import annotations

import json
import os
from pathlib import Path

from deckhand import amend, sections
from tests.conftest import FIXTURES, ROOT, run_deckhand

STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
FAKE_GH = ROOT / "tests" / "fakes" / "gh"
ISSUE = json.loads((FIXTURES / "issue.json").read_text(encoding="utf-8"))
REVIEW = json.loads((FIXTURES / "issue-reviewed.json").read_text(encoding="utf-8"))
BODY = ISSUE["body"]
NOTES = sections.get(BODY, "Notes")
REVIEWED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-reviewed.json")}
NOTE = "the grant lookup retries once when the store times out"
NEXT = "Next: /deckhand:next 248 when it reads right."
FROZEN = (
    "deckhand amend apply: the plan is frozen once the story is In Progress; "
    "record the change under Notes or open a new issue with --new-issue\n"
)
SPLIT_NEXT = "Next: carry on; /deckhand:next 999 after #248 merges."
TITLE = "Index the grant table on principal"
CHANGED_PLAN = sections.replace(BODY, "Plan", "### Task 1: A different approach\n\n- [ ] **Step 1: Write the test**")


def _status_reads(tmp_path: Path, status: str) -> dict[str, str]:
    """An env where the board's field-values read answers `status` for Status."""
    data = json.loads((FIXTURES / "graphql-fieldvalues.json").read_text(encoding="utf-8"))
    for node in data["data"]["node"]["fieldValues"]["nodes"]:
        if (node.get("field") or {}).get("name") == "Status":
            node["name"] = status
    path = tmp_path / f"fieldvalues-{status.lower().replace(' ', '-')}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_FIELDVALUES_FILE": str(path)}


def _wrapper(tmp_path: Path, script: str) -> dict[str, str]:
    """A gh on PATH ahead of the fake that runs `script` (bash) and can fall through with `exec`."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    (fake_bin / "gh").write_text(script)
    (fake_bin / "gh").chmod(0o755)
    return {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}


def _draft(tmp_path: Path, text: str = BODY) -> str:
    path = tmp_path / "248-body.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _bodies(copy: Path) -> dict[str, str]:
    """Every body file the fake recorded, by the `<verb>` of its marker line."""
    bodies: dict[str, str] = {}
    for chunk in copy.read_text(encoding="utf-8").split("--- issue ")[1:]:
        verb, _, text = chunk.partition("\n")
        bodies[verb] = text
    return bodies


def _story(tmp_path: Path, name: str, *, notes: str | None = None, review: str | None = None) -> dict[str, str]:
    """An env pointing the fake gh at the story, with `notes` as its Notes and `review` as its review comment.

    A story asked for without a review is the plain fixture, which carries no review comment at all.
    """
    data = json.loads(json.dumps(REVIEW if review is not None else ISSUE))
    if notes is not None:
        data["body"] = sections.replace(data["body"], "Notes", notes)
    if review is not None:
        data["comments"][-1]["body"] = review
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_ISSUE_FILE": str(path)}


def _raw(body: str, author: str = "arjan", at: str = "2026-09-03T09:00:00Z") -> dict[str, object]:
    """One comment as the REST endpoint reports it; the process reads its own comments from there."""
    return {"body": body, "created_at": at, "updated_at": at, "user": {"login": author}}


def _comments(tmp_path: Path, name: str, *raws: dict[str, object]) -> dict[str, str]:
    """An env where the REST comments endpoint answers with `raws`, oldest first."""
    path = tmp_path / name
    path.write_text(json.dumps(list(raws)), encoding="utf-8")
    return {"GH_COMMENTS_FILE": str(path)}


def _writes(gh_calls) -> list[str]:
    """The recorded calls that write, with the temp body-file path cut off."""
    starts = ("issue create", "issue edit", "issue comment", "api -X POST")
    return [call.split(" --body-file")[0] for call in gh_calls() if call.startswith(starts)]


# --- context ----------------------------------------------------------------


def test_context_prints_body_review_and_draft_path(fake_gh, tmp_path):
    result = run_deckhand("amend", "context", "248", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:3] == ["Status: Backlog", "", "## Body"]
    assert lines[3] == "### Story"
    assert lines[lines.index("## Latest review") - 1] == ""
    review = REVIEW["comments"][-1]["body"].strip("\n").splitlines()
    assert lines[lines.index("## Latest review") + 1 :][: len(review)] == review
    assert lines[-2] == f"Draft: {tmp_path / 'cache' / 'widgets' / '248-body.md'}"
    assert lines[-1] == "Write the whole edited body to the draft; keep every section heading."


def test_context_prints_the_body_without_the_fold(fake_gh, tmp_path):
    """The draft is the body the model edits, so the fold the issue is written with never reaches it."""
    env = _story(tmp_path, "folded.json", notes=NOTES)
    assert "<details>" in json.loads(Path(env["GH_ISSUE_FILE"]).read_text(encoding="utf-8"))["body"]

    result = run_deckhand("amend", "context", "248", env=env)

    assert result.returncode == 0, result.stderr
    assert "<details>" not in result.stdout
    assert "### Task 1: Store method" in result.stdout


def test_context_says_when_a_story_is_off_the_board(fake_gh, tmp_path):
    """The plan is frozen by a column, so the context says which column, or that there is none."""
    env = _wrapper(
        tmp_path,
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"node(id:"* ]]; then echo \'{"data":{"node":{"fieldValues":{"nodes":[]}}}}\'; exit 0; fi\n'
        f'exec "{FAKE_GH}" "$@"\n',
    )

    result = run_deckhand("amend", "context", "248", env={**REVIEWED, **env})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "Status: off the board"


def test_context_reads_the_board_once(fake_gh, gh_calls):
    result = run_deckhand("amend", "context", "248", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    assert len([call for call in gh_calls() if "node(id:" in call]) == 1


def test_context_prints_none_without_a_review(fake_gh):
    result = run_deckhand("amend", "context", "248")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("## Latest review") + 1] == "  none"


def test_context_lists_the_feedback_left_since_the_last_amend(fake_gh, tmp_path):
    """The comments are the amendment now, so the ones a person wrote after the last amend are printed."""
    env = {
        **REVIEWED,
        **_comments(
            tmp_path,
            "comments.json",
            _raw("## Review\n\n- [ ] **chaos.1, P2** A claim.", "reviewer-bot", "2026-09-02T09:00:00Z"),
            _raw("Amended: an earlier pass", "mattjmoran", "2026-09-02T10:00:00Z"),
            _raw("Drop the second criterion.\nThe third one stands."),
        ),
    }

    result = run_deckhand("amend", "context", "248", env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    start = lines.index("## Feedback")
    assert lines[start - 1] == ""
    assert lines[start + 1 : start + 4] == [
        "- arjan: Drop the second criterion.",
        "  The third one stands.",
        "",
    ]
    draft = f"Draft: {tmp_path / 'cache' / 'widgets' / '248-body.md'}"
    assert lines.index("## Latest review") < start < lines.index(draft)


def test_context_leaves_out_the_comments_the_process_wrote(fake_gh, tmp_path):
    env = {
        **REVIEWED,
        **_comments(
            tmp_path,
            "own-comments.json",
            _raw("## Review\n\n- [ ] **chaos.1, P2** A claim.", "reviewer-bot", "2026-09-02T09:00:00Z"),
            _raw("Split: #999 Index the grant table on principal, blocked by this story.", "mattjmoran"),
            _raw("Deviation: the retry path moved into the store.", "mattjmoran"),
        ),
    }

    result = run_deckhand("amend", "context", "248", env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("## Feedback") + 1] == "  none"


def test_context_prints_none_without_feedback(fake_gh):
    result = run_deckhand("amend", "context", "248")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index("## Feedback") + 1] == "  none"


def test_context_never_fails(fake_gh, tmp_path):
    env = _wrapper(tmp_path, "#!/usr/bin/env bash\necho nope >&2\nexit 1\n")

    result = run_deckhand("amend", "context", "248", env=env)

    assert result.returncode == 0
    assert result.stderr == ""
    out = result.stdout
    assert "Status: unavailable (nope)" in out
    assert "## Body\n  unavailable (nope)" in out
    assert "## Latest review\n  unavailable (nope)" in out
    assert "## Feedback\n  unavailable (nope)" in out
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


def test_apply_writes_the_body_then_posts_the_amend_as_a_comment(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "body-copy.md"

    result = run_deckhand(
        "amend", "apply", "248", _draft(tmp_path), "--note", NOTE, env={"GH_BODY_FILE_COPY": str(copy)}
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Updated #248 https://github.com/acme/widgets/issues/248",
        "Commented on #248",
        NEXT,
    ]
    bodies = _bodies(copy)
    assert bodies["edit"] == sections.render(*sections.parse(BODY))
    assert bodies["comment"] == f"Amended: {NOTE}"
    assert _writes(gh_calls) == [
        "issue edit 248 --repo acme/widgets",
        "issue comment 248 --repo acme/widgets",
    ]


def test_apply_names_the_edited_issue_before_the_comment_write(fake_gh, gh_calls, tmp_path):
    result = run_deckhand(
        "amend", "apply", "248", _draft(tmp_path), "--note", NOTE, env={"GH_ISSUE_COMMENT_FAILS": "1"}
    )

    assert result.returncode == 1
    # Flushed as it is printed: the body is already edited, whatever goes wrong after it.
    assert result.stdout.splitlines() == ["Updated #248 https://github.com/acme/widgets/issues/248"]
    assert "could not add comment" in result.stderr
    assert _writes(gh_calls) == ["issue edit 248 --repo acme/widgets", "issue comment 248 --repo acme/widgets"]


def test_the_drafts_notes_reach_the_issue_unchanged(fake_gh, tmp_path):
    """The record lives in the comments, so nothing the command owns is written into Notes."""
    copy = tmp_path / "body-copy.md"
    drafted = sections.replace(BODY, "Notes", "- the grant table is indexed on principal")

    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path, drafted),
        "--note",
        NOTE,
        env={**REVIEWED, "GH_BODY_FILE_COPY": str(copy)},
    )

    assert result.returncode == 0, result.stderr
    notes = sections.get(_bodies(copy)["edit"], "Notes")
    assert notes == "- the grant table is indexed on principal"
    assert "Amended" not in notes
    assert "Review of" not in notes


def test_apply_ends_the_same_way_while_the_story_is_running(fake_gh, tmp_path):
    result = run_deckhand(
        "amend", "apply", "248", _draft(tmp_path), "--note", NOTE, env=_status_reads(tmp_path, "In Progress")
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == NEXT


def test_apply_ends_the_same_way_off_the_board(fake_gh, tmp_path):
    none = tmp_path / "no-item.json"
    none.write_text('{"data":{"repository":{"issue":{"projectItems":{"nodes":[]}}}}}', encoding="utf-8")

    result = run_deckhand("amend", "apply", "248", _draft(tmp_path), "--note", NOTE, env={"GH_GRAPHQL_FILE": str(none)})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == NEXT


def test_apply_ends_the_same_way_when_the_status_read_fails(fake_gh, gh_calls, tmp_path):
    env = _wrapper(
        tmp_path,
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"node(id:"* ]]; then echo "the field read failed" >&2; exit 1; fi\n'
        f'exec "{FAKE_GH}" "$@"\n',
    )

    result = run_deckhand("amend", "apply", "248", _draft(tmp_path, CHANGED_PLAN), "--note", NOTE, env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == NEXT
    assert _writes(gh_calls) == [
        "issue edit 248 --repo acme/widgets",
        "issue comment 248 --repo acme/widgets",
    ]


def test_apply_reads_the_status_before_it_writes_the_body(fake_gh, gh_calls, tmp_path):
    """The freeze turns on the status, so it is read before anything is written."""
    result = run_deckhand("amend", "apply", "248", _draft(tmp_path), "--note", NOTE)

    assert result.returncode == 0, result.stderr
    calls = gh_calls()
    edit = next(i for i, call in enumerate(calls) if call.startswith("issue edit 248"))
    values = next(i for i, call in enumerate(calls) if "fieldValues" in call)
    assert values < edit


def test_the_next_line_is_the_one_command_a_person_types():
    """Every amend ends the same way, whatever the board says: the person reads it, then runs next."""
    assert amend.next_line(248) == NEXT


def test_apply_refuses_before_it_reads_the_status(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("amend", "apply", "248", _draft(tmp_path), "--note", "   ")

    assert result.returncode == 1
    assert _writes(gh_calls) == []
    assert [call for call in gh_calls() if "fieldValues" in call] == []


# --- apply, the frozen plan ---------------------------------------------------


def test_amend_refuses_a_plan_change_when_in_progress(fake_gh, gh_calls, tmp_path):
    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path, CHANGED_PLAN),
        "--note",
        NOTE,
        env=_status_reads(tmp_path, "In Progress"),
    )

    assert result.returncode == 1
    assert result.stderr == FROZEN
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_trailing_whitespace_alone_is_not_a_plan_change(fake_gh, gh_calls, tmp_path):
    padded = BODY.replace("### Task 1: Store method", "### Task 1: Store method  ")
    assert padded != BODY

    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path, padded),
        "--note",
        NOTE,
        env=_status_reads(tmp_path, "In Progress"),
    )

    assert result.returncode == 0, result.stderr
    assert _writes(gh_calls) == [
        "issue edit 248 --repo acme/widgets",
        "issue comment 248 --repo acme/widgets",
    ]


def test_amend_accepts_a_plan_change_when_backlog(fake_gh, gh_calls, tmp_path):
    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path, CHANGED_PLAN),
        "--note",
        NOTE,
        env=_status_reads(tmp_path, "Backlog"),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == NEXT
    assert _writes(gh_calls) == [
        "issue edit 248 --repo acme/widgets",
        "issue comment 248 --repo acme/widgets",
    ]


def test_amend_accepts_a_plan_change_off_the_board(fake_gh, gh_calls, tmp_path):
    none = tmp_path / "no-item.json"
    none.write_text('{"data":{"repository":{"issue":{"projectItems":{"nodes":[]}}}}}', encoding="utf-8")

    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path, CHANGED_PLAN),
        "--note",
        NOTE,
        env={"GH_GRAPHQL_FILE": str(none)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == NEXT
    assert _writes(gh_calls) == [
        "issue edit 248 --repo acme/widgets",
        "issue comment 248 --repo acme/widgets",
    ]


def test_the_freeze_leaves_a_new_issue_alone(fake_gh, gh_calls, tmp_path):
    body = (FIXTURES / "body-valid.md").read_text(encoding="utf-8")

    result = run_deckhand(
        "amend",
        "apply",
        "248",
        _draft(tmp_path, body),
        "--new-issue",
        TITLE,
        env=_status_reads(tmp_path, "In Progress"),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == SPLIT_NEXT
    assert [call for call in gh_calls() if "fieldValues" in call] == []


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
        SPLIT_NEXT,
    ]
    assert _writes(gh_calls) == [
        f"issue create --repo acme/widgets --title {TITLE}",
        "api -X POST repos/acme/widgets/issues/999/dependencies/blocked_by -F issue_id=5099965156",
        "issue comment 248 --repo acme/widgets",
    ]
    bodies = _bodies(copy)
    assert bodies["create"] == sections.render(*sections.parse(body))
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
    assert _bodies(copy)["comment"] == "Amended: the grant lookup retries once"


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
