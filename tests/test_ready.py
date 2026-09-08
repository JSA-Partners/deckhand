from __future__ import annotations

import json
import os
from pathlib import Path

from deckhand import ready
from tests.conftest import FIXTURES, ROOT, run_deckhand

FAKE_GH = ROOT / "tests" / "fakes" / "gh"
APPROVED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-approved.json")}
REVIEWED = {"GH_ISSUE_FILE": str(FIXTURES / "issue-reviewed.json")}
STUB = {"GH_ISSUE_FILE": str(FIXTURES / "stub.json")}
NO_WAIT = {"DECKHAND_SETTLE": "0"}
ONE_BLOCKER = json.dumps([{"number": 240, "title": "Grant store", "state": "open"}])
HEADER = "| # | Repo | Title | Estimate | Actual | Tasks | Note |"
RULE = "| --- | --- | --- | --- | --- | --- | --- |"
NEXT = "Next: /deckhand:start 248 when it is at the top of the Backlog."


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
    return run_deckhand("ready", "apply", "248", *args, env={**APPROVED, **NO_WAIT, **(env or {})})


def _blocked(env: dict[str, str] | None = None) -> dict[str, str]:
    """An env where GitHub reports one open dependency on the story."""
    return {"GH_BLOCKED_BY": ONE_BLOCKER, **(env or {})}


def _writes(gh_calls) -> list[str]:
    return [c for c in gh_calls() if "item-add" in c or "item-edit" in c or "-X POST" in c]


# --- the analogy table ------------------------------------------------------


def test_the_table_lists_done_stories_newest_first_with_estimate_actual_tasks(fake_gh):
    result = run_deckhand("ready", "context", "248")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[lines.index(HEADER) :] == [
        HEADER,
        RULE,
        "| 211 | widgets | Publication tier assignments | 3 | - | 0 | uncalibrated |",
        "| 210 | widgets | Tag filters on the mention list | 5 | 8 | 2 |  |",
        "| 96 | gadgets | CSV export of the breakdown chart | 8 | 5 | 1 |  |",
    ]


def test_the_table_queries_a_user_owned_project_under_user(fake_gh, gh_calls, tmp_path, monkeypatch):
    items = json.loads((FIXTURES / "graphql-project-items.json").read_text(encoding="utf-8"))
    items["data"] = {"user": items["data"].pop("organization")}
    page = tmp_path / "user-items.json"
    page.write_text(json.dumps(items), encoding="utf-8")
    monkeypatch.setenv("GH_LINKED_PROJECTS", str(FIXTURES / "linked-user.json"))
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(page))

    result = run_deckhand("ready", "context", "248")

    assert result.returncode == 0, result.stderr
    assert "| 211 | widgets | Publication tier assignments | 3 | - | 0 | uncalibrated |" in result.stdout
    (call,) = [c for c in gh_calls() if "projectV2(number" in c]
    assert "user(login:$owner)" in call
    assert "-f owner=mjm -F number=4" in call
    assert "organization(login" not in call


def test_the_table_escapes_pipes_in_titles(fake_gh, tmp_path):
    def edit(nodes):
        nodes[0]["content"]["title"] = "A | B"
        del nodes[1:]

    result = run_deckhand("ready", "context", "248", env=_items_file(tmp_path, "items-pipe.json", edit))

    assert result.returncode == 0, result.stderr
    row = result.stdout.splitlines()[-1]
    assert "A \\| B" in row


def test_the_table_tolerates_a_null_repository_name(fake_gh, tmp_path):
    def edit(nodes):
        nodes[0]["content"]["repository"]["nameWithOwner"] = None
        del nodes[1:]

    result = run_deckhand("ready", "context", "248", env=_items_file(tmp_path, "items-null-repo.json", edit))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1].startswith("| 210 |  | ")


def test_the_table_prints_whole_numbers_without_a_decimal_point(fake_gh, tmp_path):
    def edit(nodes):
        del nodes[1:]
        for value in nodes[0]["fieldValues"]["nodes"]:
            if "number" in value:
                value["number"] = float(value["number"])
        nodes[0]["fieldValues"]["nodes"][1]["number"] = 2.5

    result = run_deckhand("ready", "context", "248", env=_items_file(tmp_path, "items-float.json", edit))

    assert result.stdout.splitlines()[-1] == ("| 210 | widgets | Tag filters on the mention list | 2.5 | 8 | 2 |  |")


def test_the_table_names_the_pagination_variable_end_cursor_so_gh_paginate_advances(fake_gh, gh_calls):
    assert "after:$endCursor" in ready.ITEMS_QUERY
    assert "$after" not in ready.ITEMS_QUERY

    run_deckhand("ready", "context", "248")

    (call,) = [c for c in gh_calls() if c.startswith("api graphql") and "projectV2(number" in c]
    assert "--paginate" in call and "--slurp" in call


def test_the_table_reads_every_page(fake_gh, tmp_path):
    items = json.loads((FIXTURES / "graphql-project-items.json").read_text(encoding="utf-8"))
    nodes = items["data"]["organization"]["projectV2"]["items"]["nodes"]
    pages = []
    for i, chunk in enumerate((nodes[:2], nodes[2:])):
        page = json.loads(json.dumps(items))
        page["data"]["organization"]["projectV2"]["items"]["nodes"] = chunk
        page["data"]["organization"]["projectV2"]["items"]["pageInfo"] = {"hasNextPage": i == 0, "endCursor": "c1"}
        path = tmp_path / f"page{i}.json"
        path.write_text(json.dumps(page), encoding="utf-8")
        pages.append(str(path))

    result = run_deckhand("ready", "context", "248", env={"GH_PAGES": ":".join(pages)})

    assert result.returncode == 0, result.stderr
    rows = [line for line in result.stdout.splitlines() if line.startswith("| ") and line != HEADER]
    assert [row.split(" | ")[0] for row in rows[1:]] == ["| 211", "| 210", "| 96"]


def test_the_table_stops_at_the_limit():
    nodes = [
        {
            "content": {
                "number": n,
                "closedAt": f"2026-08-{n:02d}T00:00:00Z",
                "title": f"S{n}",
                "body": "",
                "repository": {"nameWithOwner": "acme/widgets"},
            },
            "fieldValues": {"nodes": [{"name": "Done", "field": {"name": "Status"}}]},
        }
        for n in range(1, 26)
    ]
    pages = [{"data": {"organization": {"projectV2": {"items": {"nodes": nodes}}}}}]

    rows = ready.analogy_rows(pages, ready.LIMIT)

    assert len(rows) == ready.LIMIT
    assert rows[0].startswith("| 25 |")


# --- context ----------------------------------------------------------------


def test_context_reports_blockers_fields_approval_and_the_table(fake_gh):
    result = run_deckhand("ready", "context", "248", env={**APPROVED, "GH_BLOCKED_BY": ONE_BLOCKER})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[:11] == [
        "Blockers:",
        "  240  Grant store",
        "Fields:",
        "  Kind: feat",
        "  Story Points: 3",
        "  Actual: unset",
        "Approval:",
        "  approved by lead on 2026-09-02",
        "Done stories (last 20):",
        HEADER,
        RULE,
    ]


def test_context_reads_every_field_in_one_pass(fake_gh, gh_calls):
    result = run_deckhand("ready", "context", "248", env=APPROVED)

    assert result.returncode == 0, result.stderr
    assert len([c for c in _calls(gh_calls) if c == "api graphql field-values"]) == 1


def test_context_reads_the_project_link_once(fake_gh, gh_calls):
    result = run_deckhand("ready", "context", "248", env=APPROVED)

    assert result.returncode == 0, result.stderr
    assert len([c for c in gh_calls() if "projectsV2(first" in c]) == 1


def test_context_reports_waiting_after_a_review(fake_gh):
    result = run_deckhand("ready", "context", "248", env=REVIEWED)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[1] == "  none"
    assert lines[7] == "  waiting: no Approved comment after the review of 2026-09-02"


def test_context_reports_no_review_yet(fake_gh):
    result = run_deckhand("ready", "context", "248")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[7] == "  no review yet"


def test_context_degrades_each_block_on_its_own(fake_gh, tmp_path):
    env = _wrapper(
        tmp_path,
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"node(id:"* ]]; then echo "the field read failed" >&2; exit 1; fi\n'
        f'exec "{FAKE_GH}" "$@"\n',
    )

    result = run_deckhand("ready", "context", "248", env={**APPROVED, **env})

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[1] == "  none"
    assert lines[3] == "  unavailable (the field read failed)"
    assert lines[5] == "  approved by lead on 2026-09-02"
    assert HEADER in lines


def test_context_prints_every_heading_when_gh_is_unusable(fake_gh, tmp_path):
    env = _wrapper(tmp_path, "#!/usr/bin/env bash\necho nope >&2\nexit 1\n")

    result = run_deckhand("ready", "context", "248", env=env)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert [line for line in lines if not line.startswith("  ")] == [
        "Blockers:",
        "Fields:",
        "Approval:",
        "Done stories (last 20):",
    ]
    assert lines.count("  unavailable (nope)") == 4


# --- apply ------------------------------------------------------------------


def test_apply_refuses_a_stub(fake_gh, gh_calls):
    result = run_deckhand("ready", "apply", "57", "--kind", "feat", "--points", "3", env=STUB)

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: #57 is a stub; run /deckhand:new 57 first\n"
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
    assert result.stderr == "deckhand ready apply: points must be a non-negative integer, got '-3'\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_without_a_review(fake_gh, gh_calls):
    result = _apply("--kind", "feat", "--points", "3", env={"GH_ISSUE_FILE": str(FIXTURES / "issue.json")})

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: no review comment on #248; run /deckhand:review 248\n"
    assert _writes(gh_calls) == []


def test_apply_refuses_without_approval_after_the_review(fake_gh, gh_calls):
    result = _apply("--kind", "feat", "--points", "3", env=REVIEWED)

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: no Approved comment after the review of 2026-09-02\n"
    assert _writes(gh_calls) == []


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


def test_apply_records_dependencies_adds_the_item_and_sets_the_fields(fake_gh, gh_calls, tmp_path):
    result = _apply(
        "--kind", "feat", "--points", "3", "--blocked-by", "240", env=_blocked(_status_reads(tmp_path, "Blocked"))
    )

    assert result.returncode == 0, result.stderr
    assert _calls(gh_calls) == [
        "repo view --json nameWithOwner",
        "issue view 248 --repo acme/widgets --json number,title,body,url,state,comments",
        "issue view 240 --repo acme/widgets --json state,body",
        "api repos/acme/widgets/issues/240",
        "api -X POST repos/acme/widgets/issues/248/dependencies/blocked_by -F issue_id=5099965156",
        "api graphql linked-projects",
        "project item-add 2 --owner acme --url https://github.com/acme/widgets/issues/248 --format json",
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
        "--single-select-option-id opt_blocked",
        "api graphql item-id",
        "api graphql field-values",
    ]


def test_apply_sets_blocked_when_a_blocker_is_open(fake_gh, tmp_path):
    result = _apply(
        "--kind", "feat", "--points", "3", "--blocked-by", "240", env=_blocked(_status_reads(tmp_path, "Blocked"))
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Blocked by #240",
        "Added to the board",
        "Kind=feat",
        "Story Points=3",
        "Status=Blocked",
        NEXT,
    ]


def test_apply_sets_blocked_from_a_dependency_it_was_not_given(fake_gh, tmp_path):
    result = _apply("--kind", "feat", "--points", "3", env=_blocked(_status_reads(tmp_path, "Blocked")))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Added to the board",
        "Kind=feat",
        "Story Points=3",
        "Status=Blocked",
        NEXT,
    ]


def test_apply_records_a_repeated_blocker_once(fake_gh, gh_calls, tmp_path):
    result = _apply(
        "--kind",
        "feat",
        "--points",
        "3",
        "--blocked-by",
        "240",
        "--blocked-by",
        "240",
        env=_blocked(_status_reads(tmp_path, "Blocked")),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "Blocked by #240"
    assert result.stdout.count("Blocked by #240") == 1
    assert len([c for c in gh_calls() if c.startswith("api -X POST")]) == 1


def test_apply_refuses_a_story_that_blocks_itself(fake_gh, gh_calls):
    result = _apply("--kind", "feat", "--points", "3", "--blocked-by", "248")

    assert result.returncode == 1
    assert result.stderr == "deckhand ready apply: #248 cannot block itself\n"
    assert _writes(gh_calls) == []


def test_apply_sets_backlog_when_nothing_blocks_the_story(fake_gh):
    result = _apply("--kind", "fix", "--points", "0")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Added to the board",
        "Kind=fix",
        "Story Points=0",
        "Status=Backlog",
        NEXT,
    ]


def test_apply_re_sets_status_once_when_the_automation_flipped_it(fake_gh, gh_calls, tmp_path):
    result = _apply(
        "--kind", "feat", "--points", "3", "--blocked-by", "240", env=_blocked(_status_reads(tmp_path, "Backlog"))
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-2:] == [
        "Status re-set to Blocked (the board's own automation had changed it)",
        NEXT,
    ]
    status_writes = [c for c in gh_calls() if "PVTSSF_STATUS" in c]
    assert (
        status_writes
        == [
            "project item-edit --id PVTI_TEST_248 --project-id PVT_TEST "
            "--field-id PVTSSF_STATUS --single-select-option-id opt_blocked"
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

    result = _apply("--kind", "feat", "--points", "3", env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-2:] == [
        "Status not verified (the field read failed)",
        NEXT,
    ]


def test_apply_names_the_next_step(fake_gh):
    result = _apply("--kind", "feat", "--points", "5")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == NEXT


def test_the_settle_wait_defaults_to_two_seconds():
    assert ready.settle_seconds() == 2.0


def test_the_settle_wait_is_never_negative(monkeypatch):
    for value in ("-5", "not a number", ""):
        monkeypatch.setenv("DECKHAND_SETTLE", value)

        assert ready.settle_seconds() >= 0.0

    monkeypatch.setenv("DECKHAND_SETTLE", "-5")
    assert ready.settle_seconds() == 0.0
