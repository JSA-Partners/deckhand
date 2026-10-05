"""The captain: the fleet, the order, the sessions, and the anomalies, in one printing."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from deckhand import captain, cli, fleet, issue

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "acme/widgets"


@pytest.fixture
def fleet_env(fake_gh, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-items.json"))
    monkeypatch.setenv("DECKHAND_SESSIONS", str(tmp_path / "sessions"))
    (tmp_path / "sessions").mkdir()
    _settled(tmp_path, monkeypatch)
    return tmp_path


def _settled(tmp_path, monkeypatch) -> None:
    """Satisfy the two setup facts the board cannot carry, so a fleet owes nothing by default.

    A fresh project has the Status workflows on and no marker label, and the anomalies block reports
    both, which would put a setup row under every fleet a test reads.
    """
    data = json.loads((FIXTURES / "graphql-workflows.json").read_text(encoding="utf-8"))
    for node in data["data"]["organization"]["projectV2"]["workflows"]["nodes"]:
        node["enabled"] = node["name"] == "Auto-archive items"
    path = tmp_path / "workflows-off.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("GH_WORKFLOWS_FILE", str(path))
    monkeypatch.setenv("GH_LABELS", json.dumps([{"name": "deckhand"}]))


def _nodes() -> list[dict]:
    data = json.loads((FIXTURES / "captain-items.json").read_text(encoding="utf-8"))
    return data["data"]["organization"]["projectV2"]["items"]["nodes"]


def _clean_nodes() -> list[dict]:
    data = json.loads((FIXTURES / "captain-clean.json").read_text(encoding="utf-8"))
    return data["data"]["organization"]["projectV2"]["items"]["nodes"]


def _items(tmp_path, name, nodes) -> dict[str, str]:
    """The board-items fixture rebuilt from `nodes`, as env."""
    path = tmp_path / name
    path.write_text(json.dumps({"data": {"organization": {"projectV2": {"items": {"nodes": nodes}}}}}))
    return {"GH_PROJECT_ITEMS_FILE": str(path)}


def _project_fields_file(tmp_path, name, nodes):
    path = tmp_path / name
    path.write_text(json.dumps({"data": {"organization": {"projectV2": {"fields": {"nodes": nodes}}}}}))
    return str(path)


def _project_fields(tmp_path, name, status=None, kind=None, drop=()) -> dict[str, str]:
    """The project-fields fixture with Status or Kind options replaced or a field dropped, as env."""
    data = json.loads((FIXTURES / "project-fields.json").read_text(encoding="utf-8"))
    nodes = []
    for node in data["data"]["organization"]["projectV2"]["fields"]["nodes"]:
        if node["name"] in drop:
            continue
        if node["name"] == "Status" and status is not None:
            node = {**node, "options": status}
        if node["name"] == "Kind" and kind is not None:
            node = {**node, "options": kind}
        nodes.append(node)
    return {"GH_PROJECT_FIELDS_FILE": _project_fields_file(tmp_path, name, nodes)}


def test_a_context_prints_the_four_blocks(fleet_env, capsys):
    assert cli.main(["captain", "context"]) == 0
    out = capsys.readouterr().out
    assert "## Fleet" in out
    assert "## Order" in out
    assert "## Sessions" in out
    assert "## Anomalies" in out


def test_the_fleet_names_every_story_that_is_not_done(fleet_env, capsys):
    cli.main(["captain", "context"])
    out = capsys.readouterr().out
    assert "| 253 |" in out
    assert "Seed the role matrix" in out


def test_the_fleet_says_who_holds_each_story(fleet_env, capsys):
    cli.main(["captain", "context", "--only", "fleet"])
    rows = {line.split(" | ")[0]: line for line in capsys.readouterr().out.splitlines() if line.startswith("| ")}
    assert " | mjm | " in rows["| 117"]
    assert " | - | " in rows["| 253"]


def test_the_order_names_what_to_run_per_repository(fleet_env, capsys):
    cli.main(["captain", "context"])
    assert "Next per repository:" in capsys.readouterr().out


def test_the_merge_block_follows_the_order_and_ranks_the_open_pull_requests(fleet_env, monkeypatch, capsys):
    monkeypatch.setenv("GH_PR_STATE", "OPEN")
    monkeypatch.setenv("GH_PR_ISSUES", "117")
    cli.main(["captain", "context"])
    out = capsys.readouterr().out
    assert out.index("## Order") < out.index("## Merge") < out.index("## Sessions")
    lines = _block(out, "## Merge")
    assert lines[2].startswith("| 117 | widgets | #1000 | passed | current |")
    assert "\n\nMerge #1000 first; no other open pull request changes its files.\n" in out


def test_the_merge_block_can_be_asked_for_alone(fleet_env, capsys):
    cli.main(["captain", "context", "--only", "merge"])
    out = capsys.readouterr().out
    assert _block(out, "## Merge") == ["  nothing in review"]
    assert "## Fleet" not in out


def test_no_sessions_is_a_line_and_not_a_missing_block(fleet_env, capsys):
    cli.main(["captain", "context"])
    out = capsys.readouterr().out
    assert "## Sessions" in out
    assert "none" in out


def test_a_context_never_fails(fake_gh, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_VIEW_FAILS", "1")
    assert cli.main(["captain", "context"]) == 0


def test_context_can_be_asked_for_one_block(fleet_env, capsys):
    """The skill reruns this for every later question, and four blocks is a whole fleet table each time."""
    assert cli.main(["captain", "context", "--only", "sessions"]) == 0
    out = capsys.readouterr().out
    assert "## Sessions" in out
    assert "## Fleet" not in out


def _session(root: Path, name: str, records: list[dict]) -> Path:
    path = root / "acme" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def test_a_named_session_prints_its_last_prompt_and_its_last_word(fleet_env, capsys):
    _session(
        fleet_env / "sessions",
        "one",
        [
            {"type": "user", "cwd": "/x/widgets", "message": {"content": "hi"}},
            {"type": "last-prompt", "lastPrompt": "proceed"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Which kind is it?"}]}},
        ],
    )
    assert cli.main(["captain", "context", "--session", "one"]) == 0
    out = capsys.readouterr().out
    assert "Last prompt: proceed" in out
    assert "Which kind is it?" in out


def test_an_unreadable_running_list_says_so_above_the_table(fleet_env, capsys):
    cli.main(["captain", "context", "--only", "sessions"])
    assert "Running sessions could not be read; these are transcripts from the last 24 hours." in (
        capsys.readouterr().out
    )


def test_open_sessions_are_listed_without_the_fallback_line(fleet_env, monkeypatch, capsys):
    live = fleet_env / "live"
    live.mkdir()
    (live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "one", "status": "busy"}))
    monkeypatch.setenv("DECKHAND_LIVE", str(live))
    _session(fleet_env / "sessions", "one", [{"type": "user", "cwd": "/x/widgets", "message": {"content": "hi"}}])

    assert cli.main(["captain", "context", "--only", "sessions"]) == 0

    out = capsys.readouterr().out
    assert "could not be read" not in out
    assert "| one |" in out


def test_a_session_id_nobody_has_says_so(fleet_env, capsys):
    assert cli.main(["captain", "context", "--session", "z"]) == 0
    assert "no session z" in capsys.readouterr().out


def test_an_apply_with_no_flag_refuses(fleet_env, capsys):
    assert cli.main(["captain", "apply"]) == 1
    assert "--order" in capsys.readouterr().err


def test_the_order_is_written_to_the_board(fleet_env, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--order"]) == 0
    calls = "\n".join(gh_calls())
    assert calls.count("updateProjectV2ItemPosition") == 4
    assert "Ordered 4 stories" in capsys.readouterr().out


def test_a_repair_with_nothing_to_repair_refuses(fleet_env, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-clean.json"))
    assert cli.main(["captain", "apply", "--repair"]) == 1
    assert "nothing to repair" in capsys.readouterr().err


def _foreign(status: str) -> dict:
    """A board item for an issue the process never wrote to: no log, no marker, and a column of its own."""
    return {
        "id": "I_96",
        "content": {
            "number": 96,
            "title": "Client-side CSV export",
            "url": "https://github.com/acme/widgets/issues/96",
            "state": "OPEN",
            "closedAt": None,
            "repository": {"nameWithOwner": REPO},
            "labels": {"nodes": []},
            "comments": {"nodes": []},
        },
        "fieldValues": {"nodes": [{"name": status, "field": {"name": "Status"}}]},
    }


def _without_labels(nodes: list[dict]) -> list[dict]:
    """The same items with no label on any of them, as a repository nobody has backfilled holds them."""
    return [{**node, "content": {**node["content"], "labels": {"nodes": []}}} for node in nodes]


def test_a_repair_with_only_untouched_issues_refuses(fleet_env, gh_calls, monkeypatch, tmp_path, capsys):
    """Nothing the process owns is wrong, so the run refuses rather than reporting a success that wrote nothing."""
    for name, value in _items(tmp_path, "clean-foreign.json", [*_clean_nodes(), _foreign("In Review")]).items():
        monkeypatch.setenv(name, value)

    assert cli.main(["captain", "apply", "--repair"]) == 1

    assert "nothing to repair" in capsys.readouterr().err
    assert not [call for call in gh_calls() if "item-edit" in call]


def test_blocking_a_boarded_story_records_the_dependency(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--block", "253", "--by", "117"]) == 0
    calls = "\n".join(gh_calls())
    assert "api -X POST repos/acme/widgets/issues/253/dependencies/blocked_by" in calls
    assert "#253 blocked by #117" in capsys.readouterr().out


def test_unblocking_a_boarded_story_drops_the_dependency(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--unblock", "253", "--by", "117"]) == 0
    calls = "\n".join(gh_calls())
    assert "api -X DELETE repos/acme/widgets/issues/253/dependencies/blocked_by" in calls
    assert "#253 no longer blocked by #117" in capsys.readouterr().out


def test_a_closed_blocker_refuses_and_writes_nothing(fake_gh, gh_calls, monkeypatch, tmp_path, capsys):
    closed = tmp_path / "issue-117-closed.json"
    closed.write_text(json.dumps({"number": 117, "state": "CLOSED", "body": ""}), encoding="utf-8")
    monkeypatch.setenv("GH_ISSUE_FILE_117", str(closed))

    assert cli.main(["captain", "apply", "--block", "253", "--by", "117"]) == 1

    assert capsys.readouterr().err == "deckhand captain apply: #117 is closed\n"
    assert not any("dependencies/blocked_by" in call for call in gh_calls())


def test_a_cycle_refusal_names_the_reverse_edge_and_the_unblock(fake_gh, gh_calls, monkeypatch, tmp_path, capsys):
    mapped = tmp_path / "blocked-by.json"
    edge = {"117": [{"number": 253, "title": "The other story", "state": "open"}]}
    mapped.write_text(json.dumps(edge), encoding="utf-8")
    monkeypatch.setenv("GH_DEPENDENCY_FAILS", "1")
    monkeypatch.setenv("GH_BLOCKED_BY_MAP", str(mapped))

    assert cli.main(["captain", "apply", "--block", "253", "--by", "117"]) == 1

    err = capsys.readouterr().err
    assert "#117 is already blocked by #253" in err
    assert "captain apply --unblock 117 --by 253" in err


def test_block_refuses_without_by(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--block", "253"]) == 1
    assert "--by" in capsys.readouterr().err
    assert gh_calls() == []


def test_a_story_cannot_block_itself(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--block", "253", "--by", "253"]) == 1
    assert "cannot block itself" in capsys.readouterr().err
    assert not any("dependencies/blocked_by" in call for call in gh_calls())


def test_block_and_unblock_together_refuses(fake_gh, gh_calls, capsys):
    assert cli.main(["captain", "apply", "--block", "253", "--unblock", "253", "--by", "117"]) == 1
    assert "one at a time" in capsys.readouterr().err
    assert gh_calls() == []


def test_the_forecast_is_not_one_of_the_default_blocks(fleet_env, capsys):
    """The skill reruns the context for every later question, so the default must stay cheap."""
    assert cli.main(["captain", "context"]) == 0
    assert "## Forecast" not in capsys.readouterr().out


def _finished_node(number: int, closed: str) -> dict:
    started = (datetime.fromisoformat(closed) - timedelta(hours=24)).isoformat()
    return {
        "id": f"I_{number}",
        "isArchived": True,
        "content": {
            "number": number,
            "title": "Done",
            "url": f"https://github.com/{REPO}/issues/{number}",
            "state": "CLOSED",
            "closedAt": closed,
            "body": "",
            "repository": {"nameWithOwner": REPO},
            "labels": {"nodes": [{"name": "deckhand"}]},
            "comments": {
                "nodes": [
                    {"body": "Started: on the branch", "createdAt": started, "author": {"login": "claude"}},
                    {"body": "Pull request: opened", "createdAt": closed, "author": {"login": "claude"}},
                ]
            },
        },
        "fieldValues": {"nodes": [{"name": "Done", "field": {"name": "Status"}}]},
    }


def _paced_board(tmp_path, monkeypatch) -> None:
    """The forecast fixture's open story beside nine weeks of finishes, two in odd weeks and one in even."""
    monkeypatch.setattr(captain, "_today", lambda: date(2026, 10, 2))
    data = json.loads((FIXTURES / "captain-forecast.json").read_text(encoding="utf-8"))
    nodes = [
        node for node in data["data"]["organization"]["projectV2"]["items"]["nodes"] if node["content"]["number"] == 253
    ]
    for index, days in enumerate([0, 7, 8, 14, 21, 22, 28, 35, 36, 42, 49, 50, 56]):
        noon = datetime.combine(date(2026, 10, 2) - timedelta(days=days), datetime.min.time()) + timedelta(hours=12)
        nodes.append(_finished_node(600 + index, noon.astimezone().isoformat()))
    for name, value in _items(tmp_path, "paced.json", nodes).items():
        monkeypatch.setenv(name, value)


def test_the_forecast_says_when_the_board_is_likely_done_in_weeks(fleet_env, monkeypatch, capsys):
    _paced_board(fleet_env, monkeypatch)

    assert cli.main(["captain", "context", "--only", "forecast"]) == 0

    out = capsys.readouterr().out
    assert out.split("## Forecast\n", 1)[1].splitlines() == [
        "  1 story left",
        "",
        "  Likely done by          1 week    90th percentile",
        "  Possibly as early as    1 week    50th percentile",
        "",
        "  Paced by the board's last 8 weeks, an average of 1.5 stories a week.",
        "",
        "  Cannot finish before: 1 day (critical path through the blockers)",
    ]


def test_the_forecast_prints_no_worst_case_and_no_parallelism(fleet_env, monkeypatch, capsys):
    _paced_board(fleet_env, monkeypatch)

    assert cli.main(["captain", "context", "--only", "forecast"]) == 0

    out = capsys.readouterr().out
    assert "Worst" not in out and "at once" not in out and "Commitment" not in out


def test_a_short_history_says_why_there_is_no_range(fleet_env, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-forecast.json"))
    monkeypatch.setattr(captain, "_today", lambda: date(2026, 10, 2))

    assert cli.main(["captain", "context", "--only", "forecast"]) == 0

    assert capsys.readouterr().out.split("## Forecast\n", 1)[1].splitlines() == [
        "  1 story left",
        "",
        "  No story finished on the board in the weeks measured, so there is no date range.",
        "",
        "  Cannot finish before: 1 day (critical path through the blockers)",
    ]


def test_the_forecast_takes_no_session_count(fleet_env, capsys):
    with pytest.raises(SystemExit):
        cli.main(["captain", "context", "--only", "forecast", "--sessions", "3"])


def test_a_long_command_does_not_blow_out_the_session_table(fleet_env, capsys):
    _session(
        fleet_env / "sessions",
        "wordy",
        [
            {
                "type": "user",
                "cwd": "/x/widgets",
                "message": {
                    "content": "<command-name>/deckhand:new</command-name>\n"
                    "<command-args>the test refactor and idiomatic discussion we had in this session</command-args>"
                },
            }
        ],
    )
    cli.main(["captain", "context"])
    row = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("| word |"))
    assert "new the test refactor" in row
    assert len(row) < 110


def test_the_anomalies_say_when_setup_is_owed(fleet_env, monkeypatch, capsys, tmp_path):
    """After an update nothing told a person the board's shape had moved, so they ran setup blindly."""
    for name, value in _project_fields(tmp_path, "no-kind.json", drop=("Kind",)).items():
        monkeypatch.setenv(name, value)

    assert cli.main(["captain", "context", "--only", "anomalies"]) == 0

    out = capsys.readouterr().out
    assert "/deckhand:setup" in out


def test_a_board_that_is_current_says_nothing_about_setup(fleet_env, capsys):
    """A row that appears when nothing is owed is noise, and the block is read on every rerun."""
    assert cli.main(["captain", "context", "--only", "anomalies"]) == 0

    assert "/deckhand:setup" not in capsys.readouterr().out


def test_a_stray_row_keeps_the_anomalies_table_four_columns_wide(fleet_env, settings):
    def _story(number: int, *entries: str, feature: str | None = None) -> fleet.Story:
        comments = [issue.Comment(author="claude", body=body, created_at="2026-09-01T00:00:00Z") for body in entries]
        held = issue.Issue(
            number=number, title="T", body="", url="", state="OPEN", comments=comments, labels=("deckhand",)
        )
        return fleet.Story(
            number=number,
            repo=REPO,
            title="T",
            status="Backlog",
            points=1,
            closed=False,
            item=f"I_{number}",
            issue=held,
            feature=feature,
            feature_name="Pay | refund" if feature else "",
        )

    found = fleet.anomalies([_story(1, "Split: #2 Follow on.", feature="OPT_PAY"), _story(2)], {}, set(), [])

    (row,) = [line for line in captain._anomaly_rows(settings, found, set()) if line.startswith("| 2 ")]
    what = "split from a story of Pay \\| refund, but in no feature"
    assert row == f"| 2 | widgets | {what} | epic add OPT_PAY acme/widgets#2 |"
    assert len(re.findall(r"(?<!\\)\|", row)) == 5


def test_a_board_view_that_shows_the_feature_field_owes_no_setup(fleet_env, monkeypatch, capsys, tmp_path):
    data = json.loads((FIXTURES / "project-views.json").read_text(encoding="utf-8"))
    data["data"]["organization"]["projectV2"]["views"]["nodes"][0]["fields"]["nodes"].append({"name": "Feature"})
    path = tmp_path / "views-feature.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("GH_PROJECT_VIEWS_FILE", str(path))

    assert cli.main(["captain", "context", "--only", "anomalies"]) == 0

    assert "/deckhand:setup" not in capsys.readouterr().out


def test_a_field_that_could_not_be_read_is_not_reported_as_owed(fleet_env, monkeypatch, capsys, tmp_path):
    """Unreadable is not the same as missing, and sending a person to setup over a failed read is wrong."""
    monkeypatch.setenv("GH_PROJECT_FIELDS_FILE", str(tmp_path / "does-not-exist.json"))

    assert cli.main(["captain", "context", "--only", "anomalies"]) == 0

    assert "/deckhand:setup" not in capsys.readouterr().out


def _waiting(fleet_env, monkeypatch, records) -> None:
    live = fleet_env / "live"
    live.mkdir()
    (live / "1.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "one", "status": "waiting"}))
    monkeypatch.setenv("DECKHAND_LIVE", str(live))
    _session(
        fleet_env / "sessions", "one", [{"type": "user", "cwd": "/x/widgets", "message": {"content": "hi"}}, *records]
    )


def _block(out: str, heading: str) -> list[str]:
    lines = out.split("\n\n")
    return next(chunk.splitlines()[1:] for chunk in lines if chunk.startswith(heading))


def test_a_full_read_opens_with_who_is_waiting(fleet_env, capsys):
    cli.main(["captain", "context"])
    chunks = capsys.readouterr().out.split("\n\n")
    assert chunks[0].startswith("Project:")
    assert chunks[1].splitlines() == ["## Waiting on you", "  nobody"]


def test_a_waiting_session_says_what_it_asks(fleet_env, monkeypatch, capsys):
    ask = {"type": "tool_use", "id": "q1", "name": "AskUserQuestion"}
    ask["input"] = {"questions": [{"question": "Ship it?", "options": [{"label": "yes"}, {"label": "no"}]}]}
    on_story = {"cwd": "/x/widgets", "gitBranch": "feat/253-ship"}
    _waiting(fleet_env, monkeypatch, [{"type": "assistant", **on_story, "message": {"content": [ask]}}])

    cli.main(["captain", "context"])

    (line,) = _block(capsys.readouterr().out, "## Waiting on you")
    assert re.fullmatch(r"  one #253, \d+[mhd]: Ship it\? \[yes, no\]", line)


def test_a_waiting_session_on_no_story_is_free(fleet_env, monkeypatch, capsys):
    _waiting(
        fleet_env, monkeypatch, [{"type": "assistant", "message": {"content": [{"type": "text", "text": "Next?"}]}}]
    )

    cli.main(["captain", "context"])

    (line,) = _block(capsys.readouterr().out, "## Waiting on you")
    assert re.fullmatch(r"  one free, \d+[mhd]: Next\?", line)


def test_a_first_read_has_no_since_block_and_leaves_a_snapshot(fleet_env, tmp_path, monkeypatch, capsys):
    cache = tmp_path / "snapshots"
    monkeypatch.setenv("DECKHAND_CACHE", str(cache))

    cli.main(["captain", "context"])

    assert "## Since you last looked" not in capsys.readouterr().out
    assert list(cache.glob("captain-*.json"))


def _rerun_after(fleet_env, tmp_path, monkeypatch, capsys, nodes=None) -> str:
    monkeypatch.setenv("DECKHAND_CACHE", str(tmp_path / "snapshots"))
    cli.main(["captain", "context"])
    capsys.readouterr()
    if nodes is not None:
        for name, value in _items(tmp_path, "moved.json", nodes).items():
            monkeypatch.setenv(name, value)
    cli.main(["captain", "context"])
    return capsys.readouterr().out


def test_a_second_read_says_what_moved(fleet_env, tmp_path, monkeypatch, capsys):
    nodes = _nodes()
    moved = json.loads(json.dumps(nodes))
    for field in moved[0]["fieldValues"]["nodes"]:
        if field["field"]["name"] == "Status":
            was, field["name"] = field["name"], "Ready"
    number = moved[0]["content"]["number"]

    out = _rerun_after(fleet_env, tmp_path, monkeypatch, capsys, moved)

    assert _block(out, "## Since you last looked")[1] == f"    #{number} {was} -> Ready"
    assert _block(out, "## Since you last looked")[0].startswith("  Since ")


def test_an_unchanged_board_says_nothing_moved(fleet_env, tmp_path, monkeypatch, capsys):
    out = _rerun_after(fleet_env, tmp_path, monkeypatch, capsys)
    (line,) = _block(out, "## Since you last looked")
    assert line.startswith("  Nothing moved since ")


def test_one_block_reads_nothing_and_writes_nothing(fleet_env, tmp_path, monkeypatch, capsys):
    cache = tmp_path / "snapshots"
    monkeypatch.setenv("DECKHAND_CACHE", str(cache))
    cli.main(["captain", "context"])
    (snapshot,) = cache.glob("captain-*.json")
    snapshot.unlink()

    cli.main(["captain", "context", "--only", "fleet"])

    assert not list(cache.glob("captain-*.json"))
    cli.main(["captain", "context"])
    capsys.readouterr()
    cli.main(["captain", "context", "--only", "fleet"])
    assert "## Since you last looked" not in capsys.readouterr().out


def test_the_candidates_print_on_request_and_leave_no_snapshot(fleet_env, tmp_path, monkeypatch, capsys):
    cache = tmp_path / "snapshots"
    monkeypatch.setenv("DECKHAND_CACHE", str(cache))
    cli.main(["captain", "context"])
    assert "## Candidates" not in capsys.readouterr().out
    (snapshot,) = cache.glob("captain-*.json")
    snapshot.unlink()

    assert cli.main(["captain", "context", "--only", "candidates"]) == 0

    out = capsys.readouterr().out
    assert "| # | Repo | Title | Status | Pts | Next | Waits on | Named, no edge | Files |" in out
    assert "| 257 | widgets |" in out
    assert not list(cache.glob("captain-*.json"))


def test_other_peoples_issues_are_counted_not_listed(fleet_env, tmp_path, monkeypatch, capsys):
    for name, value in _items(tmp_path, "mixed.json", [*_nodes(), _foreign("In Review")]).items():
        monkeypatch.setenv(name, value)

    cli.main(["captain", "context", "--only", "fleet"])

    out = capsys.readouterr().out
    assert "Client-side CSV export" not in out
    assert "| 96 |" not in out
    assert out.count("1 item on the board is not deckhand's.") == 1
    assert out.index("| 253 |") < out.index("1 item on the board is not deckhand's.")


def test_an_anomaly_is_in_full_once_then_standing(fleet_env, tmp_path, monkeypatch, capsys):
    nodes = _nodes()
    nodes[0] = json.loads(json.dumps(nodes[0]))
    nodes[0]["content"]["state"], nodes[0]["content"]["closedAt"] = "CLOSED", "2026-09-12T00:00:00Z"
    number = nodes[0]["content"]["number"]
    for name, value in _items(tmp_path, "anomaly.json", nodes).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DECKHAND_CACHE", str(tmp_path / "snapshots"))

    cli.main(["captain", "context"])
    first = _block(capsys.readouterr().out, "## Anomalies")
    cli.main(["captain", "context"])
    second = _block(capsys.readouterr().out, "## Anomalies")

    assert any(row.startswith(f"| {number} |") for row in first)
    assert not any(row.startswith(f"| {number} |") for row in second)
    assert any(row.startswith(f"Standing: #{number} ") for row in second)


def test_a_named_session_read_leaves_no_snapshot(fleet_env, tmp_path, monkeypatch, capsys):
    cache = tmp_path / "snapshots"
    monkeypatch.setenv("DECKHAND_CACHE", str(cache))

    assert cli.main(["captain", "context", "--session", "z"]) == 0

    assert not list(cache.glob("captain-*.json"))


def test_an_unwritable_cache_costs_only_the_snapshot(fleet_env, tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("DECKHAND_CACHE", str(blocker))

    assert cli.main(["captain", "context"]) == 0

    out = capsys.readouterr().out
    for heading in ("## Waiting on you", "## Fleet", "## Order", "## Sessions", "## Anomalies"):
        assert heading in out
    (line,) = _block(out, "## Snapshot")
    assert line.startswith("  not saved, so the next read cannot say what moved (")


def test_a_snapshot_that_cannot_be_read_says_so_in_its_block(fleet_env, monkeypatch, capsys):
    def unreadable(settings):
        raise RuntimeError("no cache here")

    monkeypatch.setattr(captain.since, "path", unreadable)

    assert cli.main(["captain", "context"]) == 0

    out = capsys.readouterr().out
    assert _block(out, "## Since you last looked") == ["  what moved could not be read (no cache here)"]
    assert "## Fleet" in out


def test_a_board_of_only_other_peoples_issues_says_so(fleet_env, tmp_path, monkeypatch, capsys):
    for name, value in _items(tmp_path, "foreign.json", [_foreign("In Review")]).items():
        monkeypatch.setenv(name, value)

    cli.main(["captain", "context", "--only", "fleet"])

    rows = capsys.readouterr().out.split("## Fleet\n")[1].splitlines()
    assert rows[:3] == ["  nothing of deckhand's on the board", "", "1 item on the board is not deckhand's."]


def test_a_full_read_finds_the_anomalies_once(fleet_env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DECKHAND_CACHE", str(tmp_path / "snapshots"))
    calls = []
    found = fleet.anomalies

    def counted(*args, **kwargs):
        calls.append(1)
        return found(*args, **kwargs)

    monkeypatch.setattr(fleet, "anomalies", counted)

    cli.main(["captain", "context"])

    assert len(calls) == 1
