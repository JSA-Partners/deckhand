from __future__ import annotations

from pathlib import Path

import pytest

from deckhand import policy as policies
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
branch = ["auth"]
""",
    )

    policy = Policy.load(tmp_path)

    assert policy.checks == ["uv run pytest", "uv run ruff check ."]
    assert policy.sensitive_paths == ["deckhand/credentials.py"]
    assert policy.front_end is True
    assert policy.extra_lenses == {"branch": ["auth"]}


def test_tier_tools_keys_become_ints(tmp_path):
    write_policy(
        tmp_path,
        'repository_label = "acme/widgets"\n\n[tier_tools]\n"0" = ["git"]\n"3" = ["pytest"]\n',
    )

    assert Policy.load(tmp_path).tier_tools == {0: ["git"], 3: ["pytest"]}


def test_tier_tools_accepts_the_lean_tier(tmp_path):
    write_policy(tmp_path, 'repository_label = "acme/widgets"\n\n[tier_tools]\n"4" = ["lake build"]\n')

    assert Policy.load(tmp_path).tier_tools == {4: ["lake build"]}


@pytest.mark.parametrize("key", ["5", "-1", "one", "01x"])
def test_tier_tools_refuses_keys_outside_zero_to_four(tmp_path, key):
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


def test_extra_lenses_refuses_an_unknown_stage(tmp_path):
    path = write_policy(tmp_path, 'repository_label = "acme/widgets"\n\n[extra_lenses]\nsecurity = ["auth"]\n')

    with pytest.raises(Refusal) as refused:
        Policy.load(tmp_path)

    assert str(path) in refused.value.rule
    assert "security" in refused.value.rule


@pytest.mark.parametrize("name", ["lenses/auth.md", "auth.md", "../auth"])
def test_extra_lenses_refuses_a_path_for_a_name(tmp_path, name):
    path = write_policy(tmp_path, f'repository_label = "acme/widgets"\n\n[extra_lenses]\nplan = ["{name}"]\n')

    with pytest.raises(Refusal) as refused:
        Policy.load(tmp_path)

    assert str(path) in refused.value.rule
    assert name in refused.value.rule


def test_second_signoff_off_by_default(tmp_path):
    path = write_policy(tmp_path, 'repository_label = "acme/widgets"\n')
    assert Policy.load(tmp_path).second_signoff is False
    path.write_text('repository_label = "acme/widgets"\nsecond_signoff = true\n', encoding="utf-8")
    assert Policy.load(tmp_path).second_signoff is True
    path.write_text('repository_label = "acme/widgets"\nsecond_signoff = "yes"\n', encoding="utf-8")
    with pytest.raises(Refusal, match="second_signoff must be true or false"):
        Policy.load(tmp_path)


def test_policy_sandbox_domains(tmp_path):
    path = write_policy(tmp_path, 'repository_label = "acme/widgets"\n')
    assert Policy.load(tmp_path).sandbox_domains == []
    path.write_text('repository_label = "acme/widgets"\n\n[sandbox]\ndomains = ["github.com", "*.npmjs.org"]\n')
    assert Policy.load(tmp_path).sandbox_domains == ["github.com", "*.npmjs.org"]
    for bad, rule in (
        ('[sandbox]\ndomains = "github.com"\n', "sandbox.domains must be a list of strings"),
        ('[sandbox]\nhosts = ["github.com"]\n', "unknown key sandbox.hosts"),
        ('sandbox = ["github.com"]\n', "sandbox must be a table"),
    ):
        path.write_text(f'repository_label = "acme/widgets"\n{bad}')
        with pytest.raises(Refusal, match=rule):
            Policy.load(tmp_path)


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (None, ".claude/deckhand.toml is missing in clone widgets"),
        ("repository_label = [", ".claude/deckhand.toml is not valid TOML in clone widgets"),
        ('repository_label = "w"\nbogus = 1\n', "unknown key bogus in .claude/deckhand.toml of clone widgets"),
    ],
)
def test_a_load_refusal_is_described_in_words_with_the_clone_name(tmp_path: Path, text: str | None, said: str):
    clone = tmp_path / "widgets"
    clone.mkdir()
    if text is not None:
        write_policy(clone, text)
    with pytest.raises(Refusal) as refused:
        Policy.load(clone)
    assert policies.described(refused.value.rule, clone) == said
