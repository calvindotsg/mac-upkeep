"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mac_upkeep.config import (
    Config,
    TaskDef,
    _load_defaults,
    load_default_task_names,
    load_task_defs,
    resolve_variables,
)

# --- TaskDef ---


def test_task_def_defaults():
    td = TaskDef(name="test", description="A test", command="echo hello")
    assert td.frequency == "weekly"
    assert td.enabled is True
    assert td.sudo is False
    assert td.shell == ""
    assert td.require_file == ""
    assert td.timeout == 300
    assert td.detect == ""


# --- _load_defaults ---


def test_load_defaults_returns_all_tasks():
    data = _load_defaults()
    assert "tasks" in data
    assert len(data["tasks"]) == 11
    assert "brew_update" in data["tasks"]
    assert "brew_bundle" in data["tasks"]


def test_load_defaults_has_run_order():
    data = _load_defaults()
    assert "run" in data
    order = data["run"]["order"]
    assert order[0] == "brew_update"
    assert order[-1] == "brew_bundle"
    assert len(order) == 11


# --- load_default_task_names ---


def test_load_default_task_names():
    tasks, order = load_default_task_names()
    assert len(tasks) == 11
    assert tasks["brew_update"] == "Update Homebrew package database"
    assert order[0] == "brew_update"
    assert order[-1] == "brew_bundle"


# --- resolve_variables ---


def test_resolve_variables_known():
    result = resolve_variables("${HOME}/test", {"HOME": "/users/me"})
    assert result == "/users/me/test"


def test_resolve_variables_multiple():
    result = resolve_variables(
        "${BREW_PREFIX}/bin/mo ${HOME}",
        {"BREW_PREFIX": "/opt/homebrew", "HOME": "/users/me"},
    )
    assert result == "/opt/homebrew/bin/mo /users/me"


def test_resolve_variables_unknown_raises():
    import pytest

    with pytest.raises(ValueError, match="Unknown variable"):
        resolve_variables("${NONEXISTENT}/path", {"HOME": "/users/me"})


def test_resolve_variables_no_vars():
    result = resolve_variables("brew update", {"HOME": "/users/me"})
    assert result == "brew update"


# --- load_task_defs ---


def test_load_task_defs_defaults_only():
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    task_defs, run_order = load_task_defs(None, variables)
    assert len(task_defs) == 11
    assert "brew_update" in task_defs
    assert task_defs["brew_update"].command == "brew update"
    assert task_defs["brew_update"].detect == "brew"
    assert task_defs["brew_update"].frequency == "weekly"
    assert task_defs["gcloud"].frequency == "monthly"
    assert task_defs["mo_clean"].sudo is True
    assert task_defs["mo_clean"].command == "/opt/homebrew/bin/mo clean"
    assert task_defs["fisher"].shell == "fish --interactive -c"
    assert run_order[0] == "brew_update"
    assert run_order[-1] == "brew_bundle"


def test_load_task_defs_user_override():
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    user_data = {"tasks": {"gcloud": {"frequency": "weekly"}}}
    task_defs, _ = load_task_defs(user_data, variables)
    # frequency overridden, other fields unchanged
    assert task_defs["gcloud"].frequency == "weekly"
    assert task_defs["gcloud"].command == "gcloud components update --quiet"
    assert task_defs["gcloud"].detect == "gcloud"


def test_load_task_defs_user_disable():
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    user_data = {"tasks": {"gcloud": {"enabled": False}}}
    task_defs, _ = load_task_defs(user_data, variables)
    assert task_defs["gcloud"].enabled is False
    # Other tasks still enabled
    assert task_defs["brew_update"].enabled is True


def test_load_task_defs_custom_task():
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    user_data = {
        "tasks": {
            "docker_prune": {
                "description": "Prune Docker",
                "command": "docker system prune -f",
                "detect": "docker",
                "frequency": "monthly",
            }
        }
    }
    task_defs, run_order = load_task_defs(user_data, variables)
    assert "docker_prune" in task_defs
    assert task_defs["docker_prune"].command == "docker system prune -f"
    assert task_defs["docker_prune"].frequency == "monthly"
    # Custom tasks auto-appended to default order
    assert "docker_prune" in run_order
    assert run_order[-1] == "docker_prune"


def test_load_task_defs_user_run_order():
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    user_data = {"run": {"order": ["brew_update", "gcloud"]}}
    _, run_order = load_task_defs(user_data, variables)
    assert run_order == ["brew_update", "gcloud"]


def test_load_task_defs_env_override(monkeypatch):
    monkeypatch.setenv("MAC_UPKEEP_GCLOUD", "false")
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    task_defs, _ = load_task_defs(None, variables)
    assert task_defs["gcloud"].enabled is False
    assert task_defs["brew_update"].enabled is True


def test_load_task_defs_env_frequency_override(monkeypatch):
    monkeypatch.setenv("MAC_UPKEEP_GCLOUD_FREQUENCY", "weekly")
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    task_defs, _ = load_task_defs(None, variables)
    assert task_defs["gcloud"].frequency == "weekly"


# --- Detect auto-inference ---


def test_detect_auto_inference_simple():
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    user_data = {
        "tasks": {
            "docker_prune": {
                "description": "Prune Docker",
                "command": "docker system prune -f",
                "frequency": "monthly",
            }
        }
    }
    task_defs, _ = load_task_defs(user_data, variables)
    assert task_defs["docker_prune"].detect == "docker"


def test_detect_auto_inference_with_variables():
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    # mo_purge has no explicit detect in defaults.toml — wait, it does.
    # Use a custom task with a path command to test variable resolution + inference
    user_data = {
        "tasks": {
            "custom_bin": {
                "description": "Run custom",
                "command": "${BREW_PREFIX}/bin/custom arg1",
                "frequency": "weekly",
            }
        }
    }
    task_defs, _ = load_task_defs(user_data, variables)
    assert task_defs["custom_bin"].detect == "/opt/homebrew/bin/custom"


def test_detect_preserves_explicit():
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    task_defs, _ = load_task_defs(None, variables)
    # gcloud has explicit detect="gcloud" in defaults.toml
    assert task_defs["gcloud"].detect == "gcloud"
    # brew_update has explicit detect="brew"
    assert task_defs["brew_update"].detect == "brew"


# --- Validation ---


def test_validation_empty_command():
    import pytest

    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    user_data = {
        "tasks": {
            "bad_task": {
                "description": "No command",
                "command": "",
                "frequency": "weekly",
            }
        }
    }
    with pytest.raises(ValueError, match="has no command"):
        load_task_defs(user_data, variables)


def test_validation_invalid_frequency():
    import pytest

    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    user_data = {
        "tasks": {
            "bad_freq": {
                "description": "Bad frequency",
                "command": "echo hello",
                "frequency": "daily",
            }
        }
    }
    with pytest.raises(ValueError, match="must be 'weekly' or 'monthly'"):
        load_task_defs(user_data, variables)


def test_validation_run_order_unknown_task():
    import pytest

    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    user_data = {"run": {"order": ["brew_update", "nonexistent_task"]}}
    with pytest.raises(ValueError, match="unknown task 'nonexistent_task'"):
        load_task_defs(user_data, variables)


# --- Custom task ordering ---


def test_custom_task_appended_to_default_order():
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    user_data = {
        "tasks": {
            "my_task": {
                "description": "My task",
                "command": "echo hello",
                "frequency": "weekly",
            }
        }
    }
    _, run_order = load_task_defs(user_data, variables)
    # Custom task appended after all 11 defaults
    assert "my_task" in run_order
    assert run_order[-1] == "my_task"
    assert len(run_order) == 12


def test_custom_task_not_duplicated_with_explicit_order():
    variables = {"BREW_PREFIX": "/opt/homebrew", "BREWFILE": "", "HOME": "/users/me"}
    user_data = {
        "tasks": {
            "my_task": {
                "description": "My task",
                "command": "echo hello",
                "frequency": "weekly",
            }
        },
        "run": {"order": ["brew_update", "my_task"]},
    }
    _, run_order = load_task_defs(user_data, variables)
    assert run_order == ["brew_update", "my_task"]
    assert run_order.count("my_task") == 1


# --- Config.load ---


def test_load_nonexistent_config_returns_defaults():
    config = Config.load(Path("/nonexistent/config.toml"))
    assert len(config.task_defs) == 11
    assert config.run_order[0] == "brew_update"
    assert config.is_enabled("brew_update") is True
    assert config.get_frequency("gcloud") == "monthly"


def test_config_is_enabled():
    config = Config.load(Path("/nonexistent/config.toml"))
    assert config.is_enabled("brew_update") is True
    assert config.is_enabled("unknown_task") is True  # unknown defaults to True


def test_config_get_frequency():
    config = Config.load(Path("/nonexistent/config.toml"))
    assert config.get_frequency("brew_update") == "weekly"
    assert config.get_frequency("gcloud") == "monthly"
    assert config.get_frequency("unknown_task") == "weekly"  # unknown defaults


def test_env_var_disables_task(monkeypatch):
    monkeypatch.setenv("MAC_UPKEEP_GCLOUD", "false")
    config = Config.load(Path("/nonexistent/config.toml"))
    assert not config.is_enabled("gcloud")
    assert config.is_enabled("pnpm")


def test_env_var_overrides_config(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[tasks.gcloud]\nenabled = true\n")

    with patch.dict("os.environ", {"MAC_UPKEEP_GCLOUD": "false"}):
        config = Config.load(config_file)
    assert not config.is_enabled("gcloud")


def test_toml_config_disables_task(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[tasks.pnpm]\nenabled = false\n")

    config = Config.load(config_file)
    assert not config.is_enabled("pnpm")
    assert config.is_enabled("gcloud")


def test_toml_config_overrides_frequency(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('[tasks.gcloud]\nfrequency = "weekly"\n')

    config = Config.load(config_file)
    assert config.get_frequency("gcloud") == "weekly"


def test_brewfile_from_env(monkeypatch):
    monkeypatch.setenv("MAC_UPKEEP_BREWFILE", "/custom/Brewfile")
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
    monkeypatch.setenv("MAC_UPKEEP_NOTIFY", "false")
    config = Config.load(Path("/nonexistent/config.toml"))
    assert config.notify is False


def test_notify_env_var_overrides_toml(tmp_path, monkeypatch):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[notifications]\nenabled = true\n")
    monkeypatch.setenv("MAC_UPKEEP_NOTIFY", "0")
    config = Config.load(config_file)
    assert config.notify is False


def test_frequency_env_override(monkeypatch):
    monkeypatch.setenv("MAC_UPKEEP_GCLOUD_FREQUENCY", "weekly")
    config = Config.load(Path("/nonexistent/config.toml"))
    assert config.get_frequency("gcloud") == "weekly"
