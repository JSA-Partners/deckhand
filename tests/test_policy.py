from __future__ import annotations

from pathlib import Path

import pytest

from deckhand.policy import Policy
from deckhand.step import Refusal


def write_policy(repo_root: Path, text: str) -> Path:
    path = repo_root / ".claude" / "deckhand.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_policy_defaults_when_optional_fields_missing(tmp_path):
    write_policy(tmp_path, 'repository_label = "acme/widgets"\n')

    policy = Policy.load(tmp_path)

    assert policy.repository_label == "acme/widgets"
    assert policy.tier_tools == {}
    assert policy.checks == []
    assert policy.sensitive_paths == []
    assert policy.extra_lenses == {}
    assert policy.front_end is False


def test_policy_reads_every_field(tmp_path):
    write_policy(
        tmp_path,
        """
repository_label = "acme/widgets"
checks = ["uv run pytest", "uv run ruff check ."]
sensitive_paths = ["deckhand/credentials.py"]
front_end = true

[tier_tools]
"0" = ["git"]
"3" = ["gh"]

[extra_lenses]
security = ["lenses/auth.md"]
""",
    )

    policy = Policy.load(tmp_path)

    assert policy.checks == ["uv run pytest", "uv run ruff check ."]
    assert policy.sensitive_paths == ["deckhand/credentials.py"]
    assert policy.front_end is True
    assert policy.extra_lenses == {"security": ["lenses/auth.md"]}


def test_tier_tools_keys_become_ints(tmp_path):
    write_policy(
        tmp_path,
        'repository_label = "acme/widgets"\n\n[tier_tools]\n"0" = ["git"]\n"3" = ["pytest"]\n',
    )

    assert Policy.load(tmp_path).tier_tools == {0: ["git"], 3: ["pytest"]}


@pytest.mark.parametrize("key", ["4", "5", "-1", "one", "01x"])
def test_tier_tools_refuses_keys_outside_zero_to_three(tmp_path, key):
    path = write_policy(
        tmp_path,
        f'repository_label = "acme/widgets"\n\n[tier_tools]\n"{key}" = ["git"]\n',
    )

    with pytest.raises(Refusal) as refused:
        Policy.load(tmp_path)

    assert str(path) in refused.value.rule
    assert "tier_tools" in refused.value.rule
    assert key in refused.value.rule


def test_policy_missing_label_refuses_naming_the_file_and_key(tmp_path):
    path = write_policy(tmp_path, 'checks = ["uv run pytest"]\n')

    with pytest.raises(Refusal) as refused:
        Policy.load(tmp_path)

    assert str(path) in refused.value.rule
    assert "repository_label" in refused.value.rule


def test_unknown_key_refuses(tmp_path):
    path = write_policy(
        tmp_path,
        'repository_label = "acme/widgets"\nchecsk = ["uv run pytest"]\n',
    )

    with pytest.raises(Refusal) as refused:
        Policy.load(tmp_path)

    assert str(path) in refused.value.rule
    assert "checsk" in refused.value.rule


def test_wrong_value_type_refuses_naming_the_key(tmp_path):
    path = write_policy(tmp_path, 'repository_label = "acme/widgets"\nfront_end = "yes"\n')

    with pytest.raises(Refusal) as refused:
        Policy.load(tmp_path)

    assert str(path) in refused.value.rule
    assert "front_end" in refused.value.rule


def test_malformed_toml_refuses_naming_the_file(tmp_path):
    path = write_policy(tmp_path, 'repository_label = "acme/widgets\n')

    with pytest.raises(Refusal) as refused:
        Policy.load(tmp_path)

    assert str(path) in refused.value.rule


def test_missing_file_refuses_naming_the_file(tmp_path):
    with pytest.raises(Refusal) as refused:
        Policy.load(tmp_path)

    assert str(tmp_path / ".claude" / "deckhand.toml") in refused.value.rule
