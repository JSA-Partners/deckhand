from __future__ import annotations

import itertools
import json
from dataclasses import replace
from pathlib import Path

import pytest

from deckhand import lint, log, naming, new, sections, step, stub
from deckhand.config import BODY_LIMIT
from deckhand.step import Refusal
from tests.conftest import FIXTURES, ROOT, run_deckhand

VALID = FIXTURES / "body-valid.md"
INVALID = FIXTURES / "body-invalid.md"
STORY_TITLE = "See only the collections I was granted"
WANT = "to see only the collections I was granted"
# The fake answers the item lookup with PVTI_TEST_248 for any issue number, so the edit names it.
STATUS_DRAFT = (
    "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTSSF_STATUS "
    "--single-select-option-id opt_draft"
)
NO_PROJECT = "deckhand new apply: no project is linked to acme/widgets; run /deckhand:setup"
UNLINKED = {"GH_LINKED_PROJECTS": str(FIXTURES / "linked-none.json")}


def _draft_writes(number: int) -> list[str]:
    """The writes that put a written story on the board as Draft and open its log."""
    return [
        f"project item-add 2 --owner acme --url https://github.com/acme/widgets/issues/{number} --format json",
        STATUS_DRAFT,
        f"issue comment {number} --repo acme/widgets",
    ]


def _calls(calls: list[str]) -> list[str]:
    """The recorded calls with the body file path dropped, since it is a temp file's name."""
    return [call.split(" --body-file")[0] for call in calls]


def _valid() -> str:
    return VALID.read_text(encoding="utf-8")


def _written() -> str:
    """The valid body as a command writes it: every section rendered, so the plan carries its fold."""
    return sections.render(*sections.parse(_valid()))


def _without(name: str) -> str:
    """The valid body with one section dropped."""
    preamble, parsed = sections.parse(_valid())
    return sections.render(preamble, [pair for pair in parsed if pair[0] != name])


def _reversed_sections() -> str:
    preamble, parsed = sections.parse(_valid())
    return sections.render(preamble, list(reversed(parsed)))


def _printed_rules(lines: list[str]) -> list[str]:
    """The bullets under `Rules:`, up to the first line that is not one."""
    after = lines[lines.index("Rules:") + 1 :]
    return list(itertools.takewhile(lambda line: line.startswith("- "), after))


def _criteria(bullet: str) -> str:
    return sections.replace(_valid(), "Acceptance Criteria", bullet)


def _want(clause: str) -> str:
    """The valid body with the Story's I want clause replaced by `clause`."""
    return _valid().replace(WANT, clause)


# Each printed rule, keyed by a phrase that appears in exactly one of them, with bodies that break
# it. A rule lint stops enforcing orphans its printed line, and this table is what catches that.
BROKEN = {
    "once each and in that order": [_without("Notes"), _valid() + "\n### Notes\n\n- again\n", _reversed_sections()],
    "at most": [_valid() + "x" * BODY_LIMIT],
    "As a <role>": [sections.replace(_valid(), "Story", "Guests should see fewer collections.")],
    "#### Out": [sections.replace(_valid(), "Scope", "#### In\n\n- Filtering\n\n#### Out")],
    "Given, When, Then": [
        _criteria("- Guests see only the collections they were granted"),
        _criteria("- Given a guest, when they list collections, then the tests pass"),
        _criteria("- Given a guest, when they list collections, then the admin UI is out of scope"),
    ],
    "### Task 1": [sections.replace(_valid(), "Plan", "### Step 1: List collections by grant")],
}


def test_context_prints_the_draft_path_skeleton_and_rules(fake_gh, tmp_path):
    result = run_deckhand("new", "context")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"Draft: {tmp_path / 'cache' / 'widgets' / 'new.md'}"
    assert lines[1] == ""
    assert [line for line in lines if line.startswith("###")] == [
        "### Story",
        "### Scope",
        "#### In",
        "#### Out",
        "### Acceptance Criteria",
        "### Plan",
        "### Notes",
    ]
    rules = _printed_rules(lines)
    assert len(rules) == len(new.rules())
    assert lines[lines.index("Rules:") - 1] == ""
    assert any("so that" in rule for rule in rules)
    assert any("Out" in rule and "empty" in rule for rule in rules)
    assert any("Given" in rule and "Then" in rule for rule in rules)
    assert any("Task 1" in rule for rule in rules)
    assert any("Notes" in rule for rule in rules)


def test_context_still_prints_when_the_repository_is_unknown(no_real_gh, tmp_path):
    result = run_deckhand("new", "context", env={"DECKHAND_CACHE": str(tmp_path / "cache")})

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Draft: unavailable (")
    assert "### Story" in result.stdout
    assert "#### Out" in result.stdout
    assert "Rules:" in result.stdout


def test_apply_refuses_a_missing_file(fake_gh, gh_calls, tmp_path):
    result = run_deckhand("new", "apply", str(tmp_path / "nope.md"))

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand new apply: cannot read ")
    assert result.stdout == ""
    assert gh_calls() == []


def test_apply_refuses_a_bad_body_and_writes_nothing(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", str(INVALID))

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand new apply: body: ")
    assert "Story is not one" in result.stderr
    assert result.stdout == ""
    assert gh_calls() == []


def test_apply_creates_the_issue_with_the_written_body(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "body-copy.md"

    result = run_deckhand("new", "apply", str(VALID), env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    (call,) = [c for c in gh_calls() if c.startswith("issue create")]
    assert call.startswith(f"issue create --repo acme/widgets --title {STORY_TITLE} --body-file ")
    assert copy.read_text(encoding="utf-8") == "--- issue create\n" + _written() + "--- issue comment\n" + new.DRAFTED


def test_apply_boards_the_new_story_as_draft_and_logs_it(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "comment.md"

    result = run_deckhand("new", "apply", str(VALID), env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Created #999 https://github.com/acme/widgets/issues/999",
        "Added to the board",
        "Status=Draft",
        "Logged Drafted",
    ]
    assert _calls([c for c in gh_calls() if c.startswith(("project item", "issue comment"))]) == _draft_writes(999)
    assert copy.read_text(encoding="utf-8").endswith("--- issue comment\n" + new.DRAFTED)


def test_the_drafted_lines_are_entries_the_log_reads_back():
    """Each note opens with a prefix and says what happened, so the log never skips it."""
    assert log.checked(new.DRAFTED) == new.DRAFTED
    assert log.checked(new.STUB_DRAFTED) == new.STUB_DRAFTED


def test_apply_refuses_before_the_first_write_when_no_project_is_linked(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", str(VALID), env=UNLINKED)

    assert result.returncode == 1
    assert result.stderr.strip() == NO_PROJECT
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_folds_the_plan_in_the_issue_body(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "body-copy.md"

    result = run_deckhand("new", "apply", str(VALID), env={"GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    written = copy.read_text(encoding="utf-8")
    assert written.count("<details>") == 1
    assert sections.get(written, "Plan") == sections.get(_valid(), "Plan")


def test_apply_title_comes_from_the_flag(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", str(VALID), "--title", "Guest filtering")

    assert result.returncode == 0, result.stderr
    (call,) = [c for c in gh_calls() if c.startswith("issue create")]
    assert "--title Guest filtering --body-file " in call


def test_apply_refuses_a_derived_title_the_subject_cannot_carry(fake_gh, gh_calls, tmp_path):
    clause = " ".join(["see every collection"] * 4)
    draft = tmp_path / "draft.md"
    draft.write_text(_want(f"to {clause}"), encoding="utf-8")

    result = run_deckhand("new", "apply", str(draft))

    assert result.returncode == 1
    assert result.stderr.strip() == (
        f"deckhand new apply: title is {len(clause)} characters; "
        f"the pull request subject allows {naming.title_limit()}; give the issue a shorter title with --title"
    )
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_refuses_a_title_flag_the_subject_cannot_carry(fake_gh, gh_calls):
    over = "x" * (naming.title_limit() + 1)

    result = run_deckhand("new", "apply", str(VALID), "--title", over)

    assert result.returncode == 1
    assert result.stderr.strip() == (
        f"deckhand new apply: title is {len(over)} characters; "
        f"the pull request subject allows {naming.title_limit()}; give the issue a shorter title with --title"
    )
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_takes_a_title_of_exactly_the_limit(fake_gh, gh_calls):
    exact = "x" * naming.title_limit()

    result = run_deckhand("new", "apply", str(VALID), "--title", exact)

    assert result.returncode == 0, result.stderr
    (call,) = [c for c in gh_calls() if c.startswith("issue create")]
    assert f"--title {exact} --body-file " in call


def test_context_prints_the_title_rule_with_its_limit(fake_gh):
    result = run_deckhand("new", "context")

    assert result.returncode == 0, result.stderr
    (rule,) = [line for line in _printed_rules(result.stdout.splitlines()) if "--title" in line]
    assert str(naming.title_limit()) in rule


def test_a_title_naming_rejects_is_refused_here(fake_gh, gh_calls):
    # Not reachable through the CLI: lint runs first, and it already requires the I want clause.
    with pytest.raises(Refusal, match="no title"):
        new.title(None, "### Story\n\nGuests should see fewer collections.\n")
    with pytest.raises(Refusal, match="pull request subject"):
        step.fits_title("x" * (naming.title_limit() + 1))

    assert gh_calls() == []


def test_apply_refuses_a_draft_that_is_not_utf_8(fake_gh, gh_calls, tmp_path):
    draft = tmp_path / "draft.md"
    draft.write_bytes(b"\xff")

    result = run_deckhand("new", "apply", str(draft))

    assert result.returncode == 1
    assert result.stderr.strip().endswith("it is not valid UTF-8")
    assert result.stdout == ""
    assert gh_calls() == []


def test_every_printed_rule_is_one_that_is_enforced(fake_gh):
    result = run_deckhand("new", "context")

    assert _printed_rules(result.stdout.splitlines()) == [f"- {rule}" for rule in new.rules()]
    # The one printed rule lint does not carry; `new.title` is what refuses a title over the limit.
    (title_rule,) = [rule for rule in new.rules() if rule not in new.RULES]
    assert "--title" in title_rule
    with pytest.raises(Refusal, match="pull request subject"):
        new.title("x" * (naming.title_limit() + 1), _valid())
    covered = set()
    for phrase, bodies in BROKEN.items():
        matched = [rule for rule in new.RULES if phrase in rule]
        assert len(matched) == 1, phrase
        covered.add(matched[0])
        for body in bodies:
            assert lint.lint(body), (phrase, body)
    # A rule that says something may be empty is a permission; every other one has to be breakable.
    assert covered == {rule for rule in new.RULES if "may be empty" not in rule}


# --- context from a source ---------------------------------------------------

STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
PARKED = {"GH_ISSUE_FILE": str(FIXTURES / "stub-parked.json")}


def test_context_from_a_file_prints_its_requirements(fake_gh, tmp_path):
    source = tmp_path / "request.md"
    source.write_text("Guests should be able to share a collection.\n", encoding="utf-8")

    result = run_deckhand("new", "context", str(source))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "## Requirements"
    assert "Guests should be able to share a collection." in lines
    assert f"Draft: {tmp_path / 'cache' / 'widgets' / 'new.md'}" in lines
    assert "### Story" in lines
    assert "Rules:" in lines


def test_context_says_so_when_an_existing_file_cannot_be_read(fake_gh, tmp_path):
    source = tmp_path / "request.md"
    source.write_bytes(b"\xff\xfe bad bytes")

    result = run_deckhand("new", "context", str(source))

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Requirements: unavailable (")
    assert "### Story" in result.stdout
    assert "Rules:" in result.stdout


def test_context_prints_a_prose_argument_as_the_requirements(fake_gh):
    result = run_deckhand("new", "context", "A release command that opens the pull request")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "## Requirements"
    assert lines[2] == "A release command that opens the pull request"
    assert "unavailable" not in result.stdout
    assert "### Story" in lines
    assert "Rules:" in lines


def test_context_does_not_repeat_a_heading_the_prose_already_has(fake_gh):
    result = run_deckhand("new", "context", "## Requirements\n\nGuests should share collections.")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "## Requirements"
    # One from the argument, one from the split file's shape; never two in a row from the argument.
    assert lines.count("## Requirements") == 2
    assert lines[1] == ""
    assert lines[2] == "Guests should share collections."


def test_context_keeps_the_line_breaks_of_a_multi_line_request(fake_gh):
    request = "Guests should share collections.\n\n- One link per collection\n- The link is revocable"

    result = run_deckhand("new", "context", request)

    assert result.returncode == 0, result.stderr
    assert request in result.stdout


def test_context_still_reads_a_file_argument(fake_gh):
    result = run_deckhand("new", "context", str(FIXTURES / "split.md"))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "## Requirements"
    assert "Guests should see only the collections they were granted." in lines
    assert str(FIXTURES / "split.md") not in result.stdout


def test_context_still_reads_a_digits_argument(fake_gh):
    result = run_deckhand("new", "context", "57", env=STUB)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "## Stub #57"


def test_context_on_a_story_says_to_run_next(fake_gh):
    result = run_deckhand("new", "context", "248")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "#248 is already a story; run /deckhand:next 248"
    assert "Rules:" in lines
    assert "### Story" not in lines


def test_context_on_a_stub_prints_the_feature_and_its_stories(fake_gh, tmp_path):
    result = run_deckhand("new", "context", "57", env=STUB)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "## Stub #57"
    assert "Guests should see only the collections they were granted." in lines
    assert "## Stories" in lines
    assert "1. #57 Grant store | Persist grants. (this one)" in lines
    assert "2. #58 Handler filter | Filter by grant. (stub)" in lines
    assert "3. #59 Admin view | Show grants. (stub)" in lines
    assert lines[lines.index("## Depends on") + 1] == "  none"
    assert f"Draft: {tmp_path / 'cache' / 'widgets' / '57-story.md'}" in lines
    assert "### Story" in lines
    assert "Rules:" in lines


def test_context_on_a_stub_survives_an_unreachable_sibling(fake_gh):
    result = run_deckhand("new", "context", "57", env={**STUB, "GH_ISSUE_VIEW_FAILS": "58"})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "2. #58 Handler filter | Filter by grant. (unavailable)" in lines
    assert "3. #59 Admin view | Show grants. (stub)" in lines
    assert "Rules:" in lines


def test_context_on_a_parked_feature_says_to_split_it(fake_gh, tmp_path):
    result = run_deckhand("new", "context", "60", env=PARKED)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "## Parked feature #60"
    assert "Guests should be able to share a collection with another guest, without an admin in the loop." in lines
    assert f"Split file: {tmp_path / 'cache' / 'widgets' / '60-split.md'}" in lines
    assert _split_skeleton(lines)
    assert lines[-1] == (
        "This is a parked feature: settle its requirements, then write the split file and run "
        "new apply --split <file> --from 60."
    )


# --- apply --stub ------------------------------------------------------------


CHANGES = ("issue create", "issue edit", "issue close", "issue comment", "project item-add", "project item-edit")


def _writes(calls: list[str]) -> list[str]:
    """Every recorded call that changed anything; after a refusal the whole log has to be reads."""
    return [call for call in calls if call.startswith(CHANGES) or "-X POST" in call]


def test_apply_stub_writes_the_drafted_story_into_the_stub(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "body-copy.md"

    result = run_deckhand("new", "apply", "--stub", "57", str(VALID), env={**STUB, "GH_BODY_FILE_COPY": str(copy)})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Written #57 https://github.com/acme/widgets/issues/57",
        "Added to the board",
        "Status=Draft",
        "Logged Drafted",
    ]
    assert _calls(_writes(gh_calls())) == ["issue edit 57 --repo acme/widgets", *_draft_writes(57)]
    assert copy.read_text(encoding="utf-8") == (
        "--- issue edit\n" + _written() + "--- issue comment\n" + new.STUB_DRAFTED
    )


def test_apply_stub_refuses_before_the_first_write_when_no_project_is_linked(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--stub", "57", str(VALID), env={**STUB, **UNLINKED})

    assert result.returncode == 1
    assert result.stderr.strip() == NO_PROJECT
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_stub_refuses_a_story_and_writes_nothing(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--stub", "248", str(VALID))

    assert result.returncode == 1
    assert result.stderr.strip() == "deckhand new apply: #248 is already a story; run /deckhand:next 248"
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_stub_refuses_a_parked_feature_and_writes_nothing(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--stub", "60", str(VALID), env=PARKED)

    assert result.returncode == 1
    assert result.stderr.strip() == "deckhand new apply: #60 is a parked feature; run /deckhand:new 60 to split it"
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_stub_refuses_a_bad_body_and_writes_nothing(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--stub", "57", str(INVALID), env=STUB)

    assert result.returncode == 1
    assert result.stderr.startswith("deckhand new apply: body: ")
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_stub_sets_the_title_after_the_body(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--stub", "57", str(VALID), "--title", "Grant store", env=STUB)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Written #57 https://github.com/acme/widgets/issues/57",
        "Title: Grant store",
        "Added to the board",
        "Status=Draft",
        "Logged Drafted",
    ]
    body_edit, title_edit, *boarding = _writes(gh_calls())
    assert _calls(boarding) == _draft_writes(57)
    assert body_edit.startswith("issue edit 57 --repo acme/widgets --body-file ")
    assert title_edit == "issue edit 57 --repo acme/widgets --title Grant store"


# --- the split file ----------------------------------------------------------

SPLIT_NOTE = "If this is more than one story, write the split file instead."


def _split_skeleton(lines: list[str]) -> bool:
    """Whether the split file's shape is printed: both headings and the example bullet."""
    return "## Requirements" in lines and "## Stories" in lines and "- <title> | <one sentence> (after 1)" in lines


def test_context_shows_the_split_file_when_there_is_no_source(fake_gh, tmp_path):
    result = run_deckhand("new", "context")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines.index(SPLIT_NOTE) > lines.index("Rules:")
    assert f"Split file: {tmp_path / 'cache' / 'widgets' / 'split.md'}" in lines
    assert _split_skeleton(lines)


def test_context_shows_the_split_file_after_a_file(fake_gh, tmp_path):
    source = tmp_path / "request.md"
    source.write_text("Guests should be able to share a collection.\n", encoding="utf-8")

    result = run_deckhand("new", "context", str(source))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines.index(SPLIT_NOTE) > lines.index("Rules:")
    assert _split_skeleton(lines)


def test_context_on_a_stub_does_not_offer_the_split_file(fake_gh):
    result = run_deckhand("new", "context", "57", env=STUB)

    assert result.returncode == 0, result.stderr
    assert SPLIT_NOTE not in result.stdout


def test_context_takes_a_blank_source_as_none(fake_gh, tmp_path):
    result = run_deckhand("new", "context", "  ")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"Draft: {tmp_path / 'cache' / 'widgets' / 'new.md'}"
    assert "Rules:" in lines


def test_context_does_not_repeat_a_heading_the_file_already_has(fake_gh, tmp_path):
    source = tmp_path / "request.md"
    source.write_text("## Requirements\n\nGuests should share collections.\n", encoding="utf-8")

    result = run_deckhand("new", "context", str(source))

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "## Requirements"
    # One from the file, one from the split file's shape; never two in a row from the file.
    assert lines.count("## Requirements") == 2
    assert lines[1] == ""
    assert lines[2] == "Guests should share collections."


def test_context_says_so_when_the_stub_itself_cannot_be_read(fake_gh, tmp_path):
    result = run_deckhand("new", "context", "57", env={**STUB, "GH_ISSUE_VIEW_FAILS": "57"})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "## Stub #57"
    assert lines[1].startswith("  unavailable (")
    assert f"Draft: {tmp_path / 'cache' / 'widgets' / '57-story.md'}" in lines
    assert "Rules:" in lines


def _state_file(tmp_path, number: int, state: str) -> str:
    """A story fixture in the state a sibling of the stub would be in."""
    data = json.loads((FIXTURES / "issue.json").read_text())
    data["state"] = state
    path = tmp_path / f"issue-{number}.json"
    path.write_text(json.dumps(data))
    return str(path)


def test_context_shows_each_siblings_own_state(fake_gh, tmp_path):
    env = {
        **STUB,
        "GH_ISSUE_FILE_58": _state_file(tmp_path, 58, "OPEN"),
        "GH_ISSUE_FILE_59": _state_file(tmp_path, 59, "CLOSED"),
    }

    result = run_deckhand("new", "context", "57", env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "1. #57 Grant store | Persist grants. (this one)" in lines
    assert "2. #58 Handler filter | Filter by grant. (open)" in lines
    assert "3. #59 Admin view | Show grants. (closed)" in lines


def test_apply_stub_keeps_the_stubs_title_when_the_flag_is_blank(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--stub", "57", str(VALID), "--title", "   ", env=STUB)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Written #57 https://github.com/acme/widgets/issues/57",
        "Added to the board",
        "Status=Draft",
        "Logged Drafted",
    ]
    call, *_ = _writes(gh_calls())
    assert call.startswith("issue edit 57 --repo acme/widgets --body-file ")


# --- apply --split -----------------------------------------------------------

SPLIT_FILE = FIXTURES / "split.md"
NUMBERS = {"GH_NEW_ISSUE": "57,58,59"}
STORIES = ["Grant store", "Handler filter", "Admin view"]
DISPATCH = "Dispatch deckhand:author for each of #57 #58 #59 with "


def _dispatch(line: str) -> None:
    """The stubs to write and the deckhand the authors are to run: the line a split ends on."""
    assert line.startswith(DISPATCH), line
    assert Path(line.rsplit(" with ", 1)[1]) == (ROOT / "bin" / "deckhand").resolve()


def _numbered_body() -> str:
    """The stub body every story of the fixture feature ends up carrying."""
    requirements, entries = stub.parse_split(SPLIT_FILE.read_text(encoding="utf-8"))
    return stub.render(requirements, [replace(e, number=n) for e, n in zip(entries, (57, 58, 59), strict=True)])


def _bodies(copy) -> list[tuple[str, str]]:
    """Each `(call, body)` the fake gh copied out of a `--body-file`, in the order they were sent."""
    pairs = []
    for part in copy.read_text(encoding="utf-8").split("--- ")[1:]:
        call, _, body = part.partition("\n")
        pairs.append((call, body))
    return pairs


def _api(calls: list[str]) -> list[str]:
    """Every REST call; for a split that is each dependency lookup and the POST that follows it."""
    return [call for call in calls if call.startswith("api ") and not call.startswith("api graphql")]


def test_apply_split_opens_one_issue_per_story_then_numbers_and_links_them(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--split", str(SPLIT_FILE), env=NUMBERS)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:-1] == [
        "Created #57 Grant store",
        "Created #58 Handler filter",
        "Created #59 Admin view",
        "Numbered #57",
        "Added to the board",
        "Status=Draft",
        "Numbered #58",
        "Added to the board",
        "Status=Draft",
        "Numbered #59",
        "Added to the board",
        "Status=Draft",
        "#58 blocked by #57",
        "#59 blocked by #57",
        "#59 blocked by #58",
    ]
    _dispatch(lines[-1])
    calls = gh_calls()
    # Every stub is boarded as Draft with no log line: the author who writes it is the one who drafts it.
    assert _calls(_writes(calls)) == [
        *[f"issue create --repo acme/widgets --title {story}" for story in STORIES],
        *[
            write
            for number in (57, 58, 59)
            for write in (f"issue edit {number} --repo acme/widgets", *_draft_writes(number)[:2])
        ],
        "api -X POST repos/acme/widgets/issues/58/dependencies/blocked_by -F issue_id=5099965156",
        "api -X POST repos/acme/widgets/issues/59/dependencies/blocked_by -F issue_id=5099965156",
        "api -X POST repos/acme/widgets/issues/59/dependencies/blocked_by -F issue_id=5099965156",
    ]
    assert _api(calls) == [
        "api repos/acme/widgets/issues/57",
        "api -X POST repos/acme/widgets/issues/58/dependencies/blocked_by -F issue_id=5099965156",
        "api repos/acme/widgets/issues/57",
        "api -X POST repos/acme/widgets/issues/59/dependencies/blocked_by -F issue_id=5099965156",
        "api repos/acme/widgets/issues/58",
        "api -X POST repos/acme/widgets/issues/59/dependencies/blocked_by -F issue_id=5099965156",
    ]


def test_apply_split_creates_unnumbered_bodies_and_rewrites_them_numbered(fake_gh, tmp_path):
    copy = tmp_path / "body-copy.md"
    env = {**NUMBERS, "GH_BODY_FILE_COPY": str(copy)}

    result = run_deckhand("new", "apply", "--split", str(SPLIT_FILE), env=env)

    assert result.returncode == 0, result.stderr
    bodies = _bodies(copy)
    assert [call for call, _ in bodies] == ["issue create"] * 3 + ["issue edit"] * 3
    requirements, entries = stub.parse_split(SPLIT_FILE.read_text(encoding="utf-8"))
    assert {body for call, body in bodies if call == "issue create"} == {stub.render(requirements, entries)}
    assert [body for call, body in bodies if call == "issue edit"] == [_numbered_body()] * 3


def test_apply_split_from_a_parked_feature_comments_and_closes_it_last(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "body-copy.md"
    env = {**NUMBERS, **PARKED, "GH_BODY_FILE_COPY": str(copy)}

    result = run_deckhand("new", "apply", "--split", str(SPLIT_FILE), "--from", "60", env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[-2] == "Closed #60"
    _dispatch(lines[-1])
    calls = gh_calls()
    comment = next(call for call in calls if call.startswith("issue comment"))
    close = next(call for call in calls if call.startswith("issue close"))
    assert calls.index(_api(calls)[-1]) < calls.index(comment) < calls.index(close)
    assert close == "issue close 60 --repo acme/widgets"
    assert ("issue comment", "Split into #57, #58, #59.") in _bodies(copy)


def test_apply_split_refuses_a_from_that_is_not_a_parked_feature(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--split", str(SPLIT_FILE), "--from", "57", env={**NUMBERS, **STUB})

    assert result.returncode == 1
    assert result.stderr.strip() == "deckhand new apply: #57 is not a parked feature"
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_split_refuses_a_parked_feature_that_was_already_split(fake_gh, gh_calls, tmp_path):
    data = json.loads((FIXTURES / "stub-parked.json").read_text(encoding="utf-8"))
    data["state"] = "CLOSED"
    closed = tmp_path / "parked-closed.json"
    closed.write_text(json.dumps(data), encoding="utf-8")

    result = run_deckhand(
        "new",
        "apply",
        "--split",
        str(SPLIT_FILE),
        "--from",
        "60",
        env={**NUMBERS, "GH_ISSUE_FILE": str(closed)},
    )

    assert result.returncode == 1
    assert result.stderr.strip() == "deckhand new apply: #60 was already split"
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_split_refuses_a_from_that_is_a_story(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--split", str(SPLIT_FILE), "--from", "248", env=NUMBERS)

    assert result.returncode == 1
    assert result.stderr.strip() == "deckhand new apply: #248 is not a parked feature"
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_split_refuses_before_the_first_write_when_no_project_is_linked(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--split", str(SPLIT_FILE), env={**NUMBERS, **UNLINKED})

    assert result.returncode == 1
    assert result.stderr.strip() == NO_PROJECT
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_split_refuses_a_malformed_file_and_names_its_line(fake_gh, gh_calls, tmp_path):
    bad = tmp_path / "split.md"
    bad.write_text("## Requirements\n\nGuests share.\n\n## Stories\n\n- no pipe here\n", encoding="utf-8")

    result = run_deckhand("new", "apply", "--split", str(bad))

    assert result.returncode == 1
    assert result.stderr.strip() == "deckhand new apply: split file line 7: expected '<title> | <sentence>'"
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_split_leaves_what_it_created_on_the_record(fake_gh, gh_calls):
    env = {**NUMBERS, "GH_ISSUE_CREATE_FAILS_AFTER": "2"}

    result = run_deckhand("new", "apply", "--split", str(SPLIT_FILE), env=env)

    assert result.returncode == 1
    assert result.stdout.splitlines() == ["Created #57 Grant store"]
    assert "could not create issue" in result.stderr
    assert [call for call in gh_calls() if call.startswith("issue edit")] == []


def test_apply_split_names_the_dependency_it_could_not_record(fake_gh, gh_calls):
    env = {**NUMBERS, "GH_DEPENDENCY_FAILS": "1"}

    result = run_deckhand("new", "apply", "--split", str(SPLIT_FILE), env=env)

    assert result.returncode == 1
    assert result.stderr.strip() == (
        "deckhand new apply: #58 blocked by #57 failed: could not add dependency; add it by hand"
    )
    assert result.stdout.splitlines()[-3:] == ["Numbered #59", "Added to the board", "Status=Draft"]
    assert len([call for call in gh_calls() if "-X POST" in call]) == 1


def test_apply_from_without_split_is_a_usage_error(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", str(SPLIT_FILE), "--from", "60")

    assert result.returncode == 2
    assert "--from" in result.stderr
    assert gh_calls() == []


def test_apply_title_with_split_is_a_usage_error(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--split", str(SPLIT_FILE), "--title", "Guest sharing")

    assert result.returncode == 2
    assert "--title" in result.stderr
    assert gh_calls() == []


def test_apply_split_and_stub_together_is_a_usage_error(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--split", "--stub", "57", str(SPLIT_FILE))

    assert result.returncode == 2
    assert gh_calls() == []


# --- apply --park ------------------------------------------------------------

FEATURE_LINE = "Guests should be able to share a collection with another guest."
FEATURE = f"## Requirements\n\n{FEATURE_LINE}\n"


def _park_file(tmp_path, text: str):
    path = tmp_path / "feature.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_apply_park_opens_a_feature_stub_with_no_stories(fake_gh, gh_calls, tmp_path):
    copy = tmp_path / "body-copy.md"
    env = {"GH_NEW_ISSUE": "61", "GH_BODY_FILE_COPY": str(copy)}

    result = run_deckhand("new", "apply", "--park", str(_park_file(tmp_path, FEATURE)), env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["Parked #61 https://github.com/acme/widgets/issues/61"]
    (call,) = [c for c in gh_calls() if c.startswith("issue create")]
    assert f" --title {FEATURE_LINE} --body-file " in call
    ((_, body),) = _bodies(copy)
    assert body == stub.render(FEATURE_LINE, [])
    assert stub.read(body) == (FEATURE_LINE, [])


def test_apply_park_takes_the_title_flag(fake_gh, gh_calls, tmp_path):
    path = _park_file(tmp_path, FEATURE)

    result = run_deckhand("new", "apply", "--park", str(path), "--title", "Guest sharing")

    assert result.returncode == 0, result.stderr
    (call,) = [c for c in gh_calls() if c.startswith("issue create")]
    assert " --title Guest sharing --body-file " in call


def test_apply_park_refuses_a_file_that_already_lists_stories(fake_gh, gh_calls):
    result = run_deckhand("new", "apply", "--park", str(SPLIT_FILE))

    assert result.returncode == 1
    assert result.stderr.strip() == "deckhand new apply: a parked feature has no stories yet; use --split"
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_park_refuses_a_file_whose_stories_are_already_numbered(fake_gh, gh_calls, tmp_path):
    requirements, entries = stub.parse_split(SPLIT_FILE.read_text(encoding="utf-8"))
    path = _park_file(tmp_path, stub.render(requirements, entries))

    result = run_deckhand("new", "apply", "--park", str(path))

    assert result.returncode == 1
    assert result.stderr.strip() == "deckhand new apply: a parked feature has no stories yet; use --split"
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_park_refuses_a_heading_with_no_text(fake_gh, gh_calls, tmp_path):
    path = _park_file(tmp_path, "## Requirements\n\n## Stories\n")

    result = run_deckhand("new", "apply", "--park", str(path))

    assert result.returncode == 1
    assert result.stderr.strip() == "deckhand new apply: ## Requirements has no text"
    assert result.stdout == ""
    assert _writes(gh_calls()) == []


def test_apply_park_refuses_a_file_without_the_heading(fake_gh, gh_calls, tmp_path):
    path = _park_file(tmp_path, "Guests should be able to share a collection.\n")

    result = run_deckhand("new", "apply", "--park", str(path))

    assert result.returncode == 1
    assert result.stderr.strip() == "deckhand new apply: a parked feature starts with ## Requirements"
    assert result.stdout == ""
    assert _writes(gh_calls()) == []
