"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from maintenance.config import DEFAULT_FREQUENCIES, DEFAULT_TASKS, Config


def test_default_config_has_all_tasks_enabled():
    config = Config()
    for task in DEFAULT_TASKS:
        assert config.is_enabled(task)


def test_load_nonexistent_config_returns_defaults():
    config = Config.load(Path("/nonexistent/config.toml"))
    for task in DEFAULT_TASKS:
        assert config.is_enabled(task)


def test_env_var_disables_task(monkeypatch):
    monkeypatch.setenv("MAINTENANCE_GCLOUD", "false")
    config = Config.load(Path("/nonexistent/config.toml"))
    assert not config.is_enabled("gcloud")
    assert config.is_enabled("pnpm")


def test_env_var_overrides_config(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[tasks]\ngcloud = true\n")

    with patch.dict("os.environ", {"MAINTENANCE_GCLOUD": "false"}):
        config = Config.load(config_file)
    assert not config.is_enabled("gcloud")


def test_toml_config_disables_task(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[tasks]\npnpm = false\n")

    config = Config.load(config_file)
    assert not config.is_enabled("pnpm")
    assert config.is_enabled("gcloud")


def test_brewfile_from_env(monkeypatch):
    monkeypatch.setenv("MAINTENANCE_BREWFILE", "/custom/Brewfile")
    config = Config.load(Path("/nonexistent/config.toml"))
    assert config.brewfile == "/custom/Brewfile"


def test_brewfile_from_config(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('[paths]\nbrewfile = "/my/Brewfile"\n')

    config = Config.load(config_file)
    assert config.brewfile == "/my/Brewfile"


def test_notify_defaults_to_true():
    config = Config.load(Path("/nonexistent/config.toml"))
    assert config.notify is True
    assert config.notify_sound == "Submarine"


def test_notify_from_toml(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('[notifications]\nenabled = false\nsound = "Glass"\n')
    config = Config.load(config_file)
    assert config.notify is False
    assert config.notify_sound == "Glass"


def test_notify_env_var_disables(monkeypatch):
    monkeypatch.setenv("MAINTENANCE_NOTIFY", "false")
    config = Config.load(Path("/nonexistent/config.toml"))
    assert config.notify is False


def test_notify_env_var_overrides_toml(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[notifications]\nenabled = true\n")
    monkeypatch.setenv("MAINTENANCE_NOTIFY", "0")
    config = Config.load(config_file)
    assert config.notify is False


def test_default_frequencies():
    config = Config()
    for task in DEFAULT_TASKS:
        assert task in config.frequencies
        assert config.frequencies[task] in ("weekly", "monthly")


def test_frequency_from_toml(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('[frequency]\ngcloud = "monthly"\n')
    config = Config.load(config_file)
    assert config.get_frequency("gcloud") == "monthly"
    assert config.get_frequency("pnpm") == DEFAULT_FREQUENCIES["pnpm"]


def test_frequency_env_override(monkeypatch):
    monkeypatch.setenv("MAINTENANCE_GCLOUD_FREQUENCY", "monthly")
    config = Config.load(Path("/nonexistent/config.toml"))
    assert config.get_frequency("gcloud") == "monthly"


def test_get_frequency_defaults_to_weekly():
    config = Config()
    assert config.get_frequency("unknown_task") == "weekly"
