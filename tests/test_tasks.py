"""Tests for task execution."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

from maintenance.config import Config
from maintenance.tasks import run_task, strip_ansi


def test_strip_ansi_removes_color_codes():
    assert strip_ansi("\x1b[0;32m✓\x1b[0m test") == "✓ test"


def test_strip_ansi_preserves_plain_text():
    assert strip_ansi("no colors here") == "no colors here"


def test_run_task_skips_disabled():
    config = Config()
    config.tasks["gcloud"] = False
    result = run_task("gcloud", ["gcloud", "update"], config=config)
    assert result.status == "skipped"
    assert result.reason == "disabled"


@patch("maintenance.tasks.shutil.which", return_value=None)
def test_run_task_skips_missing_command(mock_which):
    config = Config()
    result = run_task("gcloud", ["gcloud", "update"], config=config)
    assert result.status == "skipped"
    assert result.reason == "not installed"


def test_run_task_dry_run_does_not_execute():
    config = Config()
    with patch("maintenance.tasks.shutil.which", return_value="/usr/bin/gcloud"):
        result = run_task("gcloud", ["gcloud", "update"], config=config, dry_run=True)
    assert result.status == "ok"
    assert result.reason == "dry-run"


@patch("maintenance.tasks.subprocess.run")
@patch("maintenance.tasks.shutil.which", return_value="/usr/bin/echo")
def test_run_task_executes_command(mock_which, mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
    config = Config()
    result = run_task("gcloud", ["echo", "test"], config=config)
    assert result.status == "ok"
    assert result.reason == ""
    assert result.duration > 0
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs["stdin"] == subprocess.DEVNULL


@patch("maintenance.tasks.subprocess.run")
@patch("maintenance.tasks.shutil.which", return_value="/usr/bin/echo")
def test_run_task_nonzero_exit_returns_failed(mock_which, mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
    config = Config()
    result = run_task("gcloud", ["echo", "test"], config=config)
    assert result.status == "failed"
    assert result.reason == "exit code 1"
    assert result.duration > 0


@patch("maintenance.tasks.subprocess.run")
@patch("maintenance.tasks.shutil.which", return_value="/usr/bin/echo")
def test_run_task_timeout_returns_failed(mock_which, mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="echo", timeout=300)
    config = Config()
    result = run_task("gcloud", ["echo", "test"], config=config)
    assert result.status == "failed"
    assert result.reason == "timed out"
