"""The captain: the fleet, the order, the sessions, and the anomalies, in one printing."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from deckhand import captain, cli, fleet

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "acme/widgets"


@pytest.fixture
def fleet_env(fake_gh, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-items.json"))
    monkeypatch.setenv("DECKHAND_SESSIONS", str(tmp_path / "sessions"))
    (tmp_path / "sessions").mkdir()
    return tmp_path


def _nodes() -> list[dict]:
    data = json.loads((FIXTURES / "captain-items.json").read_text(encoding="utf-8"))
    return data["data"]["organization"]["projectV2"]["items"]["nodes"]


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


def test_the_next_line_keeps_the_ranked_order_of_the_repositories():
    """The urgent repository leads, so the line cannot be sorted by the repository's own name."""
    found = list(fleet.stories(_nodes()))
    read = fleet.Fleet(
        stories=found,
        blockers={story.key: list(story.blocked_by) for story in found if story.status != "Done"},
        behind=set(),
        missing=[],
    )

    assert captain._next_line(read, []) == (
        "Next per repository: widgets 253 (no session open), gadgets 258 (no session open)"
    )


def test_the_next_line_names_the_draft_a_blocked_repository_waits_on():
    """A Draft is not in the order table, so the Next line is the only place its chain can be seen."""
    found = {story.number: story for story in fleet.stories(_nodes())}
    draft = replace(found[268], status="Draft")
    read = fleet.Fleet(
        stories=[found[257], draft],
        blockers={found[257].key: [("acme/widgets", 268, "Domains")], draft.key: []},
        behind=set(),
        missing=[],
    )

    assert captain._next_line(read, []) == "Next per repository: widgets: #268 is Draft, unblocking 1 story"


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


def test_a_repair_sets_the_status_the_log_allows(fleet_env, capsys):
    assert cli.main(["captain", "apply", "--repair"]) == 0
    out = capsys.readouterr().out
    assert "268" in out and "Draft" in out


def test_a_repair_with_nothing_to_repair_refuses(fleet_env, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-clean.json"))
    assert cli.main(["captain", "apply", "--repair"]) == 1
    assert "nothing to repair" in capsys.readouterr().err


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


def test_the_forecast_prints_a_floor_and_a_commitment(fleet_env, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-forecast.json"))
    assert cli.main(["captain", "context", "--only", "forecast"]) == 0
    out = capsys.readouterr().out
    assert "## Forecast" in out
    assert "Floor" in out
    assert "Commitment" in out


def test_a_thin_band_reports_the_worst_run_and_says_so(fleet_env, monkeypatch, capsys):
    """Under ten samples a percentile is a fit to noise, so the commitment is the worst thing seen."""
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-forecast.json"))

    assert cli.main(["captain", "context", "--only", "forecast"]) == 0

    out = capsys.readouterr().out
    assert "worst run, banded by points" in out
    assert "Thin history" in out


def test_a_full_band_reports_a_percentile_and_drops_the_warning(fleet_env, monkeypatch, capsys):
    """The label must track the number it describes: an 85th percentile is not the observed maximum."""
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-forecast.json"))
    monkeypatch.setattr(captain, "THIN", 1)  # the fixture's bands are enough once the bar is this low

    assert cli.main(["captain", "context", "--only", "forecast"]) == 0

    out = capsys.readouterr().out
    assert "85th percentile, banded by points" in out
    assert "observed maximum" not in out
    assert "Thin history" not in out


def test_the_forecast_never_prints_a_median(fleet_env, capsys):
    """A number on the page gets quoted, and the median forecast is the one that must not be."""
    cli.main(["captain", "context", "--only", "forecast"])
    assert "50th" not in capsys.readouterr().out


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


def test_a_field_that_could_not_be_read_is_not_reported_as_owed(fleet_env, monkeypatch, capsys, tmp_path):
    """Unreadable is not the same as missing, and sending a person to setup over a failed read is wrong."""
    monkeypatch.setenv("GH_PROJECT_FIELDS_FILE", str(tmp_path / "does-not-exist.json"))

    assert cli.main(["captain", "context", "--only", "anomalies"]) == 0

    assert "/deckhand:setup" not in capsys.readouterr().out


def test_the_forecast_says_where_its_parallelism_came_from(fleet_env, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-forecast.json"))
    cli.main(["captain", "context", "--only", "forecast", "--sessions", "3"])
    out = capsys.readouterr().out
    assert "3 at once, given" in out
    assert "A point groups stories that take about as long as each other. It is not hours." in out


def test_thin_history_forecasts_across_the_open_sessions(fleet_env, monkeypatch, capsys):
    monkeypatch.setenv("GH_PROJECT_ITEMS_FILE", str(FIXTURES / "captain-forecast.json"))
    cli.main(["captain", "context", "--only", "forecast"])
    assert "1 at once, open sessions" in capsys.readouterr().out
