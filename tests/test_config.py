from __future__ import annotations

from pathlib import Path

import pytest

from deckhand import config
from deckhand.config import LocalConfig
from deckhand.step import Refusal


def write_config(directory: Path, text: str) -> Path:
    path = directory / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_config_reads_clones_and_slot_cap(tmp_path):
    path = write_config(
        tmp_path,
        'slot_cap = 2\n\n[clones]\n"acme/widgets" = "/src/widgets"\n',
    )

    config = LocalConfig.load(path)

    assert config.clones == {"acme/widgets": Path("/src/widgets")}
    assert config.slot_cap == 2


def test_config_paths_expand_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = write_config(tmp_path, '[clones]\n"acme/widgets" = "~/code/widgets"\n')

    assert LocalConfig.load(path).clones == {"acme/widgets": tmp_path / "code" / "widgets"}


def test_slot_cap_must_be_positive(tmp_path):
    path = write_config(tmp_path, "slot_cap = 0\n")

    with pytest.raises(Refusal) as refused:
        LocalConfig.load(path)

    assert str(path) in refused.value.rule
    assert "slot_cap" in refused.value.rule


@pytest.mark.parametrize("text", ['slot_cap = "2"\n', "slot_cap = true\n"])
def test_slot_cap_must_be_an_integer(tmp_path, text):
    path = write_config(tmp_path, text)

    with pytest.raises(Refusal) as refused:
        LocalConfig.load(path)

    assert "slot_cap" in refused.value.rule


def test_unknown_key_refuses(tmp_path):
    path = write_config(tmp_path, "slot_capp = 2\n")

    with pytest.raises(Refusal) as refused:
        LocalConfig.load(path)

    assert str(path) in refused.value.rule
    assert "slot_capp" in refused.value.rule


def test_missing_file_gives_defaults(tmp_path):
    config = LocalConfig.load(tmp_path / "absent.toml")

    assert config.clones == {}
    assert config.slot_cap == 1


def test_config_reads_team_key_and_sandbox_write(tmp_path):
    path = write_config(tmp_path, 'team_key = "ACME"\nsandbox_write = ["~/.cache/uv"]\n')

    config = LocalConfig.load(path)

    assert (config.team_key, config.sandbox_write) == ("ACME", ["~/.cache/uv"])


def test_team_key_and_sandbox_write_default_empty(tmp_path):
    config = LocalConfig.load(tmp_path / "missing.toml")

    assert (config.team_key, config.sandbox_write) == ("", [])


def test_sandbox_write_must_be_strings(tmp_path):
    path = write_config(tmp_path, "sandbox_write = [1]\n")

    with pytest.raises(Refusal, match="sandbox_write must be a list of strings"):
        LocalConfig.load(path)


def test_local_reads_the_config_dir_from_the_environment(tmp_path, monkeypatch):
    write_config(tmp_path, 'team_key = "ACME"\n')
    monkeypatch.setenv("DECKHAND_CONFIG_DIR", str(tmp_path))

    assert config.config_dir() == tmp_path
    assert config.local().team_key == "ACME"
