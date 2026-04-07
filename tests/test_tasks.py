"""Tests for task execution."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from maintenance.config import Config
from maintenance.output import Output
from maintenance.tasks import _run, _should_run, _update_last_run, run_task, strip_ansi


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


# --- Frequency scheduling tests ---


def test_should_run_never_ran(tmp_path, monkeypatch):
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", tmp_path / "last-run.json")
    config = Config()
    assert _should_run("gcloud", config) is True


def test_should_run_within_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config()
    config.frequencies["gcloud"] = "weekly"
    assert _should_run("gcloud", config) is False


def test_should_run_past_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    old = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": old}))
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config()
    config.frequencies["gcloud"] = "weekly"
    assert _should_run("gcloud", config) is True


def test_should_run_monthly_within_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=15)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config()
    config.frequencies["gcloud"] = "monthly"
    assert _should_run("gcloud", config) is False


def test_should_run_corrupt_timestamp(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    state_file.write_text(json.dumps({"gcloud": "not-a-date"}))
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config()
    assert _should_run("gcloud", config) is True


def test_should_run_corrupt_json(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    state_file.write_text("{invalid json")
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config()
    assert _should_run("gcloud", config) is True


def test_update_last_run_creates_state_file(tmp_path, monkeypatch):
    state_dir = tmp_path / "maintenance"
    state_file = state_dir / "last-run.json"
    monkeypatch.setattr("maintenance.tasks._STATE_DIR", state_dir)
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    _update_last_run("gcloud")
    assert state_file.is_file()
    state = json.loads(state_file.read_text())
    assert "gcloud" in state


def test_run_frequency_skip(tmp_path, monkeypatch):
    """_run() skips task with 'ran recently' when frequency check applies (no --force)."""
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config()
    config.frequencies["gcloud"] = "weekly"
    output = Output(interactive=False)
    result = _run(
        "gcloud",
        ["gcloud", "update"],
        config=config,
        output=output,
        dry_run=False,
    )
    assert result.status == "skipped"
    assert result.reason == "ran recently"


@patch("maintenance.tasks.subprocess.run")
@patch("maintenance.tasks.shutil.which", return_value="/usr/bin/gcloud")
def test_force_bypasses_frequency(mock_which, mock_run, tmp_path, monkeypatch):
    """_run() executes forced task (filter passes, frequency bypassed)."""
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    monkeypatch.setattr("maintenance.tasks._STATE_DIR", tmp_path)
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    config = Config()
    config.frequencies["gcloud"] = "weekly"
    output = Output(interactive=False)
    result = _run(
        "gcloud",
        ["gcloud", "update"],
        config=config,
        output=output,
        dry_run=False,
        force_tasks={"gcloud"},
    )
    assert result.status == "ok"


def test_force_filters_unselected_tasks():
    """_run() skips tasks not in force_tasks with 'not selected'."""
    config = Config()
    output = Output(interactive=False)
    result = _run(
        "pnpm",
        ["pnpm", "store", "prune"],
        config=config,
        output=output,
        dry_run=False,
        force_tasks={"gcloud"},
    )
    assert result.status == "skipped"
    assert result.reason == "not selected"


def test_dry_run_no_timestamp_update(tmp_path, monkeypatch):
    """Dry-run does not update the last-run timestamp."""
    state_dir = tmp_path / "maintenance"
    state_file = state_dir / "last-run.json"
    monkeypatch.setattr("maintenance.tasks._STATE_DIR", state_dir)
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config()
    output = Output(interactive=False)
    with patch("maintenance.tasks.shutil.which", return_value="/usr/bin/gcloud"):
        result = _run(
            "gcloud",
            ["gcloud", "update"],
            config=config,
            output=output,
            dry_run=True,
        )
    assert result.status == "ok"
    assert result.reason == "dry-run"
    assert not state_file.exists()


@patch("maintenance.tasks.subprocess.run")
@patch("maintenance.tasks.shutil.which", return_value="/usr/bin/echo")
def test_failed_task_no_timestamp_update(mock_which, mock_run, tmp_path, monkeypatch):
    """Failed task does not update the last-run timestamp."""
    state_dir = tmp_path / "maintenance"
    state_file = state_dir / "last-run.json"
    monkeypatch.setattr("maintenance.tasks._STATE_DIR", state_dir)
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
    config = Config()
    output = Output(interactive=False)
    result = _run(
        "gcloud",
        ["echo", "test"],
        config=config,
        output=output,
        dry_run=False,
    )
    assert result.status == "failed"
    assert not state_file.exists()
