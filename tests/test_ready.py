from __future__ import annotations

import json
import os
from pathlib import Path

from deckhand import ready
from tests.conftest import FIXTURES, ROOT, run_deckhand, spilled

FAKE_GH = ROOT / "tests" / "fakes" / "gh"
REVIEWED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-reviewed.json")}
STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
NO_WAIT = {"DECKHAND_SETTLE": "0"}
ONE_BLOCKER = json.dumps([{"number": 240, "title": "Grant store", "state": "open"}])
POINT = "A point groups stories that take about as long as each other. It is not hours."
HEADER = "| Pts | # | Repo | Title | Hours |"
RULE = "| --- | --- | --- | --- | --- |"
ITEM_ADD = "project item-add 2 --owner acme --url https://github.com/acme/widgets/issues/248 --format json"
EDITS = [
    "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTSSF_KIND "
    "--single-select-option-id opt_feat",
    "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTF_POINTS --number 3",
    "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTSSF_STATUS "
    "--single-select-option-id opt_backlog",
]
LEGACY = {"GH_ITEM_MISSING_CALLS": "1"}


def _wrapper(tmp_path: Path, script: str) -> dict[str, str]:
    """A gh on PATH ahead of the fake that runs `script` (bash) and can fall through with `exec`."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    (fake_bin / "gh").write_text(script)
    (fake_bin / "gh").chmod(0o755)
    return {"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}


def _items_file(tmp_path: Path, name: str, edit) -> dict[str, str]:
    """The project items fixture with `edit` applied to its nodes, as an env for the fake."""
    items = json.loads((FIXTURES / "graphql-project-items.json").read_text(encoding="utf-8"))
    edit(items["data"]["organization"]["projectV2"]["items"]["nodes"])
    path = tmp_path / name
    path.write_text(json.dumps(items), encoding="utf-8")
    return {"GH_PROJECT_ITEMS_FILE": str(path)}


def _status_reads(tmp_path: Path, status: str) -> dict[str, str]:
    """An env where the field-values read answers `status` for Status."""
    data = json.loads((FIXTURES / "graphql-fieldvalues.json").read_text(encoding="utf-8"))
    for node in data["data"]["node"]["fieldValues"]["nodes"]:
        if (node.get("field") or {}).get("name") == "Status":
            node["name"] = status
    path = tmp_path / f"fieldvalues-{status.lower()}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"GH_FIELDVALUES_FILE": str(path)}


def _query_name(call: str) -> str:
    for marker, name in (
        ("projectsV2(first", "linked-projects"),
        ("projectItems(first", "item-id"),
        ("node(id:", "field-values"),
        ("projectV2(number", "project-items"),
    ):
        if marker in call:
            return name
    return "unknown"


def _calls(gh_calls) -> list[str]:
    """The recorded gh calls, each GraphQL one shortened to the name of the query it ran."""
    return [f"api graphql {_query_name(c)}" if c.startswith("api graphql") else c for c in gh_calls()]


def _apply(*args: str, env: dict[str, str] | None = None):
    return run_deckhand("ready", "apply", "248", *args, env={**REVIEWED, **NO_WAIT, **(env or {})})


def _blocked(env: dict[str, str] | None = None) -> dict[str, str]:
    """An env where GitHub reports one open dependency on the story."""
    return {"GH_BLOCKED_BY": ONE_BLOCKER, **(env or {})}


def _writes(gh_calls) -> list[str]:
    return [c for c in gh_calls() if "item-add" in c or "item-edit" in c or "-X POST" in c]


# --- the reference stories -------------------------------------------------


def _forecast_items(tmp_path: Path, name: str, edit) -> dict[str, str]:
    """The fleet fixture with finished, measured stories, with `edit` applied to its nodes."""
    items = json.loads((FIXTURES / "captain-forecast.json").read_text(encoding="utf-8"))
    edit(items["data"]["organization"]["projectV2"]["items"]["nodes"])
    path = tmp_path / name
    path.write_text(json.dumps(items), encoding="utf-8")
    return {"GH_PROJECT_ITEMS_FILE": str(path)}


def test_the_reference_table_groups_finished_stories_by_points_with_their_hours(fake_gh):
    env = {"GH_PROJECT_ITEMS_FILE": str(FIXTURES / "captain-forecast.json")}

    result = run_deckhand("ready", "context", "248", env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    start = lines.index("Reference stories:")
    assert lines[start : lines.index("## Story")] == [
        "Reference stories:",
        f"  {POINT}",
        HEADER,
        RULE,
        "| 1 | 501 | widgets | Warm the widget cache | 4.0 |",
        "| 1 | 500 | widgets | Seed the widget cache | 2.0 |",
        "| 2 | 502 | widgets | Evict the widget cache | 10.0 |",
    ]


def test_the_reference_table_keeps_three_per_point_value(fake_gh, tmp_path):
    def edit(nodes):
        done = nodes[1]
        for n in range(3):
            copy = json.loads(json.dumps(done))
            copy["content"]["number"] = 600 + n
            nodes.append(copy)

    result = run_deckhand("ready", "context", "248", env=_forecast_items(tmp_path, "many.json", edit))

    assert result.returncode == 0, result.stderr
    ones = [line for line in result.stdout.splitlines() if line.startswith("| 1 |")]
    assert len(ones) == ready.REFERENCES


def test_the_reference_table_escapes_pipes_in_titles(fake_gh, tmp_path):
    def edit(nodes):
        nodes[1]["content"]["title"] = "A | B"

    result = run_deckhand("ready", "context", "248", env=_forecast_items(tmp_path, "pipe.json", edit))

    assert "A \\| B" in result.stdout


def test_the_reference_table_queries_a_user_owned_project_under_user(fake_gh, gh_calls, tmp_path, monkeypatch):
    items = json.loads((FIXTURES / "captain-forecast.json").read_text(encoding="utf-8"))
    items["data"] = {"user": items["data"].pop("organization")}
    page = tmp_path / "user-items.json"
    page.write_text(json.dumps(items), encoding="utf-8")
    monkeypatch.setenv("GH_LINKED_PROJECTS", str(FIXTURES / "linked-user.json"))
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(page))

    result = run_deckhand("ready", "context", "248")

    assert result.returncode == 0, result.stderr
    assert "| 2 | 502 | widgets | Evict the widget cache | 10.0 |" in result.stdout
    assert all("user(login:$owner)" in c for c in gh_calls() if "projectV2(number" in c)


def test_no_measured_story_says_none_under_the_definition(fake_gh):
    result = run_deckhand("ready", "context", "248")

    lines = result.stdout.splitlines()
    start = lines.index("Reference stories:")
    assert lines[start : start + 3] == ["Reference stories:", f"  {POINT}", "  none"]


# --- context ----------------------------------------------------------------


def test_context_reports_blockers_fields_and_the_table(fake_gh):
    result = run_deckhand("ready", "context", "248", env={**REVIEWED, "GH_BLOCKED_BY": ONE_BLOCKER})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "Rules:"
    assert lines[4] == "Kinds: feat, fix, chore, refactor, docs, perf"
    assert lines[6:12] == [
        "Fields:",
        "  Kind: feat",
        "  Story Points: 3",
        "Reference stories:",
        f"  {POINT}",
        "  none",
    ]
    assert lines[12] == "## Story"
    assert lines[13].startswith("As a guest user, I want to see only")
    blockers = lines.index("Blockers:")
    assert lines[blockers : blockers + 4] == [
        "Blockers:",
        "  #240  Grant store",
        "Could block this story:",
        "  #300 Backlog Not done yet",
    ]


def test_context_says_nothing_about_approval(fake_gh):
    """The review is the only gate now, so there is no approval to report."""
    result = run_deckhand("ready", "context", "248", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    assert "Approval:" not in result.stdout
    assert "approved" not in result.stdout.lower()


def test_context_prints_the_story_the_points_are_estimated_from(fake_gh):
    """The estimate is a comparison, so the story being estimated is on the page beside the table."""
    result = run_deckhand("ready", "context", "248", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines.index("Reference stories:") < lines.index("## Story")
    assert "### Scope" not in lines
    assert lines.index("## Story") < lines.index("Blockers:")


def test_context_reads_every_field_in_one_pass(fake_gh, gh_calls):
    result = run_deckhand("ready", "context", "248", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    assert len([c for c in _calls(gh_calls) if c == "api graphql field-values"]) == 1


def test_context_reads_the_project_link_once(fake_gh, gh_calls):
    result = run_deckhand("ready", "context", "248", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    assert len([c for c in gh_calls() if "projectsV2(first" in c]) == 1


def test_context_degrades_each_block_on_its_own(fake_gh, tmp_path):
    env = _wrapper(
        tmp_path,
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"node(id:"* ]]; then echo "the field read failed" >&2; exit 1; fi\n'
        f'exec "{FAKE_GH}" "$@"\n',
    )

    result = run_deckhand("ready", "context", "248", env={**REVIEWED, **env})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    blockers = lines.index("Blockers:")
    assert lines[blockers + 1] == "  none"
    assert lines[blockers + 3] == "  #300 Backlog Not done yet"
    assert lines[lines.index("Fields:") + 1] == "  unavailable (the field read failed)"
    assert f"  {POINT}" in lines


def test_context_prints_the_plan_and_the_review_the_step_holds(fake_gh):
    """Points are estimated against the plan, and the Review: entry is the gate this step holds."""
    result = run_deckhand("ready", "context", "248", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    assert "### Task 1: Store method" in spilled(result.stdout, "Plan")
    assert "Review: sound" in result.stdout
    assert "A story boards only with a Review: entry in its log." in result.stdout


def test_context_lists_the_stories_a_blocker_could_be(fake_gh):
    """A blocker is chosen here, so the stories it could be are on the page rather than on the board."""
    result = run_deckhand("ready", "context", "248", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    listed = result.stdout.split("Could block this story:")[1]
    assert "#300" in listed
    assert "#248" not in listed


def test_context_prints_every_heading_when_gh_is_unusable(fake_gh, tmp_path):
    env = _wrapper(tmp_path, "#!/usr/bin/env bash\necho nope >&2\nexit 1\n")

    result = run_deckhand("ready", "context", "248", env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert [line for line in lines if not line.startswith("  ")] == [
        "Rules:",
        "",
        "Kinds: feat, fix, chore, refactor, docs, perf",
        "",
        "Fields:",
        "Reference stories:",
        "## Story",
        "Plan: unavailable (nope)",
        "Review:",
        "Blockers:",
        "Could block this story:",
        "",
        "Apply: deckhand ready apply 248 --kind <kind> --points <1-3>",
    ]
    assert lines.count("  unavailable (nope)") == 6


# --- apply ------------------------------------------------------------------


def test_apply_refuses_a_stub(fake_gh, gh_calls):
    result = run_deckhand("ready", "apply", "57", "--kind", "feat", "--points", "3", env=STUB)

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: #57 is a stub; run /deckhand:next 57 first\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_refuses_an_unknown_kind(fake_gh, gh_calls):
    result = _apply("--kind", "feature", "--points", "3")

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: kind must be one of: feat, fix, chore, refactor, docs, perf\n"
    assert result.stdout == ""
    assert _writes(gh_calls) == []


def test_apply_refuses_points_that_are_not_a_whole_number(fake_gh, gh_calls):
    result = _apply("--kind", "feat", "--points", "-3")

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: points must be a whole number, got '-3'\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_more_than_three_points(fake_gh, gh_calls):
    result = _apply("--kind", "feat", "--points", "4")

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand ready apply: more than 3 points is more than one story; split it with the Review section's split\n"
    )
    assert _writes(gh_calls) == []


def test_apply_refuses_zero_points(fake_gh, gh_calls):
    result = _apply("--kind", "feat", "--points", "0")

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: points start at 1\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_a_story_with_no_review_entry(fake_gh, gh_calls):
    result = run_deckhand("ready", "apply", "248", "--kind", "feat", "--points", "3")

    assert result.returncode == 1
    assert result.stderr == (
        "deckhand ready apply: A story boards only with a Review: entry in its log. Run /deckhand:next 248.\n"
    )
    assert _writes(gh_calls) == []


def test_apply_moves_a_reviewed_draft_to_backlog(fake_gh, gh_calls):
    result = _apply("--kind", "feat", "--points", "3")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["Kind=feat", "Story Points=3", "Status=Backlog"]
    assert _writes(gh_calls) == EDITS


def test_apply_adds_a_story_that_is_not_on_the_board_yet(fake_gh, gh_calls):
    """A story from before Draft existed is still boarded; the add is the one extra write."""
    result = _apply("--kind", "feat", "--points", "3", env=LEGACY)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["Added to the board", "Kind=feat", "Story Points=3", "Status=Backlog"]
    assert _writes(gh_calls) == [ITEM_ADD, *EDITS]


def test_apply_boards_a_reviewed_story_with_no_approval_reply(fake_gh):
    """The review comment is the whole gate; no reply on the issue is read as an approval."""
    result = _apply("--kind", "feat", "--points", "3", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    assert "Status=Backlog" in result.stdout


def test_apply_reads_no_approval(fake_gh, gh_calls):
    """Nothing in the run looks for an approving comment, so nothing can wait on one."""
    result = _apply("--kind", "feat", "--points", "3")

    assert result.returncode == 0, result.stderr
    assert "approv" not in result.stdout.lower()


def test_apply_refuses_a_closed_blocker(fake_gh, gh_calls, tmp_path):
    closed = tmp_path / "closed.json"
    closed.write_text(json.dumps({"number": 240, "title": "Grant store", "state": "CLOSED"}), encoding="utf-8")
    env = _wrapper(
        tmp_path,
        "#!/usr/bin/env bash\n"
        f'if [ "$1 $2 $3" = "issue view 240" ]; then cat "{closed}"; exit 0; fi\n'
        f'exec "{FAKE_GH}" "$@"\n',
    )

    result = _apply("--kind", "feat", "--points", "3", "--blocked-by", "240", env=env)

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: #240 is closed\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_a_blocker_that_does_not_exist(fake_gh, gh_calls, tmp_path):
    env = _wrapper(
        tmp_path,
        "#!/usr/bin/env bash\n"
        'if [ "$1 $2 $3" = "issue view 999" ]; then echo "could not find issue 999" >&2; exit 1; fi\n'
        f'exec "{FAKE_GH}" "$@"\n',
    )

    result = _apply("--kind", "feat", "--points", "3", "--blocked-by", "999", env=env)

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: #999 does not exist\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_a_blocker_it_cannot_read(fake_gh, gh_calls, tmp_path):
    env = _wrapper(
        tmp_path,
        "#!/usr/bin/env bash\n"
        'if [ "$1 $2 $3" = "issue view 240" ]; then echo "HTTP 502: Bad gateway" >&2; exit 1; fi\n'
        f'exec "{FAKE_GH}" "$@"\n',
    )

    result = _apply("--kind", "feat", "--points", "3", "--blocked-by", "240", env=env)

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: #240 cannot be read: HTTP 502: Bad gateway\n"
    assert _writes(gh_calls) == []


def test_apply_records_dependencies_checks_the_board_and_sets_the_fields(fake_gh, gh_calls):
    result = _apply("--kind", "feat", "--points", "3", "--blocked-by", "240", env=_blocked())

    assert result.returncode == 0, result.stderr
    assert _calls(gh_calls) == [
        "repo view --json nameWithOwner",
        "issue view 248 --repo acme/widgets --json number,title,body,url,state,comments",
        "issue view 240 --repo acme/widgets --json state,body",
        "api repos/acme/widgets/issues/240",
        "api -X POST repos/acme/widgets/issues/248/dependencies/blocked_by -F issue_id=5099965156",
        "api graphql item-id",
        "api graphql linked-projects",
        "api graphql item-id",
        "project field-list 2 --owner acme --format json",
        "project view 2 --owner acme --format json",
        "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTSSF_KIND "
        "--single-select-option-id opt_feat",
        "api graphql item-id",
        "project field-list 2 --owner acme --format json",
        "project view 2 --owner acme --format json",
        "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTF_POINTS --number 3",
        "api graphql item-id",
        "project field-list 2 --owner acme --format json",
        "project view 2 --owner acme --format json",
        "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST --field-id PVTSSF_STATUS "
        "--single-select-option-id opt_backlog",
    ]


def test_apply_boards_backlog_and_records_the_blocker_it_was_given(fake_gh):
    """Blocked is gone from the board; the dependency is still recorded, and start reads it live."""
    result = _apply("--kind", "feat", "--points", "3", "--blocked-by", "240", env=_blocked())

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Blocked by #240",
        "Kind=feat",
        "Story Points=3",
        "Status=Backlog",
    ]


def test_apply_boards_backlog_with_a_dependency_it_was_not_given(fake_gh):
    result = _apply("--kind", "feat", "--points", "3", env=_blocked())

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["Kind=feat", "Story Points=3", "Status=Backlog"]


def test_apply_records_a_repeated_blocker_once(fake_gh, gh_calls):
    result = _apply("--kind", "feat", "--points", "3", "--blocked-by", "240", "--blocked-by", "240", env=_blocked())

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "Blocked by #240"
    assert result.stdout.count("Blocked by #240") == 1
    assert len([c for c in gh_calls() if c.startswith("api -X POST")]) == 1


def test_apply_refuses_a_story_that_blocks_itself(fake_gh, gh_calls):
    result = _apply("--kind", "feat", "--points", "3", "--blocked-by", "248")

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: #248 cannot block itself\n"
    assert _writes(gh_calls) == []


def test_apply_re_sets_status_once_when_the_automation_flipped_it(fake_gh, gh_calls, tmp_path):
    result = _apply("--kind", "feat", "--points", "3", env={**LEGACY, **_status_reads(tmp_path, "In Progress")})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "Status re-set to Backlog (the board's own automation had changed it)"
    status_writes = [c for c in gh_calls() if "PVTSSF_STATUS" in c]
    assert (
        status_writes
        == [
            "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST "
            "--field-id PVTSSF_STATUS --single-select-option-id opt_backlog"
        ]
        * 2
    )


def test_apply_says_status_is_unverified_when_the_read_back_fails(fake_gh, tmp_path):
    env = _wrapper(
        tmp_path,
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"node(id:"* ]]; then echo "the field read failed" >&2; exit 1; fi\n'
        f'exec "{FAKE_GH}" "$@"\n',
    )

    result = _apply("--kind", "feat", "--points", "3", env={**LEGACY, **env})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "Status not verified (the field read failed)"


def test_the_settle_wait_defaults_to_two_seconds():
    assert ready.settle_seconds() == 2.0


def test_the_settle_wait_is_never_negative(monkeypatch):
    for value in ("-5", "not a number", ""):
        monkeypatch.setenv("DECKHAND_SETTLE", value)

        assert ready.settle_seconds() >= 0.0

    monkeypatch.setenv("DECKHAND_SETTLE", "-5")
    assert ready.settle_seconds() == 0.0


def test_apply_records_a_blocker_in_another_repository(fake_gh, gh_calls, tmp_path):
    result = _apply("--kind", "feat", "--points", "3", "--blocked-by", "acme/gadgets#9", env=_blocked())

    assert result.returncode == 0, result.stderr
    assert "Blocked by acme/gadgets#9" in result.stdout.splitlines()
    assert "api repos/acme/gadgets/issues/9" in gh_calls()
    assert "api -X POST repos/acme/widgets/issues/248/dependencies/blocked_by -F issue_id=5099965156" in gh_calls()


def test_apply_refuses_a_blocker_that_is_not_a_reference(fake_gh, gh_calls, tmp_path):
    result = _apply("--kind", "feat", "--points", "3", "--blocked-by", "acme/gadgets", env=_blocked())

    assert result.returncode == 1
    assert "owner/name#M" in result.stderr
    assert _writes(gh_calls) == []


# --- the size gate --------------------------------------------------------------

OVERSIZED = "### Story\n\nAs a maintainer, I want one thing, so that it is done.\n\n" + "x" * 50_000


def _ready(tmp_path: Path, body: str, *extra: str):
    """Board 248 with `body` as its reviewed body, plus any extra flags."""
    data = json.loads((FIXTURES / "issue-reviewed.json").read_text(encoding="utf-8"))
    data["body"] = body
    path = tmp_path / "sized.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return _apply("--kind", "feat", "--points", "2", *extra, env={"GH_ISSUE_FILE": str(path)})


def test_a_body_over_the_ceiling_is_refused_at_boarding(fake_gh, gh_calls, tmp_path):
    result = _ready(tmp_path, OVERSIZED)

    assert result.returncode == 1
    assert "needs --oversized saying why it is one story" in result.stderr
    assert "Body is 50067 characters" in result.stderr
    assert [c for c in gh_calls() if "item-edit" in c] == []


def test_oversized_boards_it_and_logs_the_reason(fake_gh, gh_calls, tmp_path):
    result = _ready(tmp_path, OVERSIZED, "--oversized", "The cutover is one change across 56 routes.")

    assert result.returncode == 0, result.stderr
    assert "Logged Noted" in result.stdout
    assert [c for c in gh_calls() if "item-edit" in c] != []


def test_a_body_under_the_ceiling_boards_untouched(fake_gh, tmp_path):
    result = _ready(tmp_path, "### Story\n\nAs a maintainer, I want one thing, so that it is done.\n")

    assert result.returncode == 0, result.stderr
    assert "Logged Noted" not in result.stdout


def test_context_names_the_apply(fake_gh):
    result = run_deckhand("ready", "context", "248")

    assert result.stdout.splitlines()[-1] == "Apply: deckhand ready apply 248 --kind <kind> --points <1-3>"
