"""Tests for CLI commands."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from maintenance.cli import app

runner = CliRunner()


def test_help_exits_zero():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "maintenance" in result.output.lower()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0


def test_setup_outputs_sudoers():
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0
    assert "NOPASSWD" in result.output
    assert "env_keep" in result.output
    assert "mo clean" in result.output
    assert "mo optimize" in result.output


def test_run_dry_run():
    result = runner.invoke(app, ["run", "--dry-run"])
    assert result.exit_code == 0


def test_tasks_command():
    result = runner.invoke(app, ["tasks"])
    assert result.exit_code == 0
    for name in ["brew_update", "gcloud", "mo_clean", "brew_bundle"]:
        assert name in result.output


def test_force_invalid_shows_valid_tasks():
    result = runner.invoke(app, ["run", "--force", "nonexistent"])
    assert result.exit_code == 1
    assert "Valid tasks:" in result.output


def test_notify_test_command_succeeds():
    from unittest.mock import patch

    with patch("maintenance.cli.notify", return_value=True):
        result = runner.invoke(app, ["notify-test"])
    assert result.exit_code == 0
    assert "Notification sent" in result.output


def test_notify_test_command_fails():
    with patch("maintenance.cli.notify", return_value=False):
        result = runner.invoke(app, ["notify-test"])
    assert result.exit_code == 1
    assert "Notification failed" in result.output


# --- init command ---


def test_init_creates_config(tmp_path):
    config_path = tmp_path / "config.toml"
    with (
        patch("maintenance.cli.DEFAULT_CONFIG_DIR", tmp_path),
        patch("maintenance.cli.DEFAULT_CONFIG_PATH", config_path),
        patch("maintenance.cli.shutil.which", return_value="/opt/homebrew/bin/brew"),
        patch("maintenance.config.get_brew_prefix", return_value="/opt/homebrew"),
    ):
        result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert config_path.is_file()
    content = config_path.read_text()
    # All-comments — no active TOML lines
    active_lines = [ln for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
    assert active_lines == []
    assert "maintenance configuration" in content
    assert "Customize" in content


def test_init_shows_detected_tasks(tmp_path):
    config_path = tmp_path / "config.toml"

    def which_side_effect(binary):
        # Only "brew" is detected (also matches resolved full path)
        return "/opt/homebrew/bin/brew" if "brew" in binary else None

    with (
        patch("maintenance.cli.DEFAULT_CONFIG_DIR", tmp_path),
        patch("maintenance.cli.DEFAULT_CONFIG_PATH", config_path),
        patch("maintenance.cli.shutil.which", side_effect=which_side_effect),
        patch("maintenance.config.get_brew_prefix", return_value="/opt/homebrew"),
    ):
        result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    content = config_path.read_text()
    assert "Detected tasks" in content
    assert "brew_update" in content
    assert "Not detected" in content
    assert "gcloud" in content


def test_init_errors_if_config_exists(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("existing content")
    with (
        patch("maintenance.cli.DEFAULT_CONFIG_DIR", tmp_path),
        patch("maintenance.cli.DEFAULT_CONFIG_PATH", config_path),
    ):
        result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "already exists" in result.output
    # Existing file unchanged
    assert config_path.read_text() == "existing content"


def test_init_force_overwrites(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("old content")
    with (
        patch("maintenance.cli.DEFAULT_CONFIG_DIR", tmp_path),
        patch("maintenance.cli.DEFAULT_CONFIG_PATH", config_path),
        patch("maintenance.cli.shutil.which", return_value=None),
    ):
        result = runner.invoke(app, ["init", "--force"])
    assert result.exit_code == 0
    assert config_path.read_text() != "old content"


# --- show-config command ---


def test_show_config_default_outputs_defaults_toml():
    result = runner.invoke(app, ["show-config", "--default"])
    assert result.exit_code == 0
    assert "[tasks.brew_update]" in result.output
    assert "[tasks.brew_bundle]" in result.output
    assert "[run]" in result.output
    assert "${BREW_PREFIX}" in result.output  # raw vars, not resolved


def test_show_config_no_user_config(tmp_path):
    config_path = tmp_path / "config.toml"
    with patch("maintenance.cli.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(app, ["show-config"])
    assert result.exit_code == 0
    assert "No config file found" in result.output
    assert "maintenance init" in result.output


def test_show_config_user_config(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[tasks.gcloud]\nenabled = false\n")
    with patch("maintenance.cli.DEFAULT_CONFIG_PATH", config_path):
        result = runner.invoke(app, ["show-config"])
    assert result.exit_code == 0
    assert "[tasks.gcloud]" in result.output
    assert "enabled = false" in result.output
