import json
import os
import subprocess
from pathlib import Path

import pytest

from deckhand import config

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def _linked(monkeypatch, name):
    monkeypatch.setenv("GH_LINKED_PROJECTS", str(FIXTURES / f"{name}.json"))


def test_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("DECKHAND_PROJECT", "7")
    monkeypatch.delenv("DECKHAND_CACHE", raising=False)
    s = config.load(tmp_path)
    assert s.project == 7
    assert s.kinds == ["feat", "fix", "chore", "refactor", "docs", "perf"]
    assert s.cache.name == "deckhand"


def test_the_default_kinds_are_conventional_commit_types():
    """A Kind names a whole story, and a story ships as commits of its own type."""
    assert set(config.DEFAULT_KINDS) <= set(config.TYPES)


def test_whitespace_only_deckhand_kinds_falls_back_to_the_default_list(monkeypatch, tmp_path):
    monkeypatch.setenv("DECKHAND_PROJECT", "7")
    monkeypatch.setenv("DECKHAND_KINDS", "   ")
    s = config.load(tmp_path)
    assert s.kinds == config.DEFAULT_KINDS


def test_the_env_override_wins_over_the_link(fake_gh, monkeypatch, tmp_path):
    monkeypatch.setenv("DECKHAND_PROJECT", "7")
    _linked(monkeypatch, "linked-one")
    s = config.load(tmp_path)
    assert s.project == 7
    assert (s.owner, s.owner_type) == ("acme", "Organization")


def test_the_settings_file_override_wins_over_the_link(fake_gh, monkeypatch, tmp_path):
    monkeypatch.delenv("DECKHAND_PROJECT", raising=False)
    _linked(monkeypatch, "linked-one")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"env": {"DECKHAND_PROJECT": "5"}}))
    assert config.load(tmp_path).project == 5


def test_env_wins_over_settings_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DECKHAND_PROJECT", "9")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"env": {"DECKHAND_PROJECT": "5"}}))
    assert config.load(tmp_path).project == 9


def test_one_linked_project_resolves_with_its_owner(fake_gh, monkeypatch, tmp_path):
    _linked(monkeypatch, "linked-one")
    s = config.load(tmp_path)
    assert (s.project, s.owner, s.owner_type) == (2, "acme", "Organization")


def test_a_user_owned_link_resolves_as_a_user(fake_gh, monkeypatch, tmp_path):
    _linked(monkeypatch, "linked-user")
    s = config.load(tmp_path)
    assert (s.project, s.owner, s.owner_type) == (4, "mjm", "User")


def test_no_linked_project_names_the_repository_and_setup(fake_gh, monkeypatch, tmp_path):
    _linked(monkeypatch, "linked-none")
    with pytest.raises(config.NoProject) as info:
        config.load(tmp_path).require_project()
    assert str(info.value) == "no project is linked to acme/widgets; run /deckhand:setup"


def test_several_linked_projects_list_them_and_name_the_override(fake_gh, monkeypatch, tmp_path):
    _linked(monkeypatch, "linked-two")
    with pytest.raises(config.ConfigError) as info:
        config.load(tmp_path).require_project()
    assert str(info.value) == (
        "2 projects are linked to acme/widgets; set DECKHAND_PROJECT to the number of the one you want:\n"
        "  acme #2 Widgets\n"
        "  acme #7 Platform"
    )


def test_an_override_that_names_a_linked_project_takes_its_owner(fake_gh, gh_calls, monkeypatch, tmp_path):
    monkeypatch.setenv("DECKHAND_PROJECT", "4")
    _linked(monkeypatch, "linked-user")
    s = config.load(tmp_path)
    assert (s.project, s.owner, s.owner_type) == (4, "mjm", "User")
    assert not [call for call in gh_calls() if call.startswith("project view")]


def test_an_override_that_is_not_linked_falls_back_to_the_repository_owner(fake_gh, monkeypatch, tmp_path):
    monkeypatch.setenv("DECKHAND_PROJECT", "7")
    monkeypatch.setenv("GH_PROJECT_OWNER_TYPE", "User")
    monkeypatch.setenv("GH_PROJECT_OWNER", "acme")
    _linked(monkeypatch, "linked-one")
    s = config.load(tmp_path)
    assert (s.project, s.owner, s.owner_type) == (7, "acme", "User")


def test_an_unlinked_override_that_does_not_exist_says_how_to_link_it(fake_gh, monkeypatch, tmp_path):
    monkeypatch.setenv("DECKHAND_PROJECT", "7")
    monkeypatch.setenv("GH_PROJECT_VIEW_FAILS", "1")
    _linked(monkeypatch, "linked-one")
    with pytest.raises(config.ConfigError) as info:
        _ = config.load(tmp_path).owner
    message = str(info.value)
    assert "first 20 projects linked to acme/widgets" in message
    assert "gh project link 7" in message
    assert "set DECKHAND_PROJECT to a linked project's number" in message


def test_the_link_is_read_once_per_process(fake_gh, gh_calls, monkeypatch, tmp_path):
    _linked(monkeypatch, "linked-one")
    s = config.load(tmp_path)
    assert (s.project, s.owner, s.owner_type, s.project) == (2, "acme", "Organization", 2)
    assert len([c for c in gh_calls() if "projectsV2(first" in c]) == 1


def test_missing_project_needs_no_gh_when_the_override_answers_it(monkeypatch, tmp_path):
    # The override alone gives the number, so nothing reaches GitHub; the autouse stub proves it.
    monkeypatch.setenv("DECKHAND_PROJECT", "4")
    assert config.load(tmp_path).require_project() == 4


def test_non_numeric_project_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("DECKHAND_PROJECT", "two")
    with pytest.raises(config.ConfigError):
        config.load(tmp_path).require_project()


def test_broken_settings_json_is_reported(monkeypatch, tmp_path):
    monkeypatch.delenv("DECKHAND_PROJECT", raising=False)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{not json")
    with pytest.raises(config.ConfigError) as info:
        config.load(tmp_path)
    assert "settings.json" in str(info.value)


def test_config_tests_are_isolated_from_ambient_env():
    """A DECKHAND_PROJECT exported in the parent shell must not leak into test_config.py."""
    env = {**os.environ, "DECKHAND_PROJECT": "999"}
    result = subprocess.run(
        [
            "uv",
            "run",
            "pytest",
            "tests/test_config.py",
            "-q",
            "--deselect",
            "tests/test_config.py::test_config_tests_are_isolated_from_ambient_env",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
