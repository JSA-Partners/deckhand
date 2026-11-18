from __future__ import annotations

from pathlib import Path

import pytest

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
