"""Tests for CLI commands."""

from __future__ import annotations

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
    from unittest.mock import patch

    with patch("maintenance.cli.notify", return_value=False):
        result = runner.invoke(app, ["notify-test"])
    assert result.exit_code == 1
    assert "Notification failed" in result.output
