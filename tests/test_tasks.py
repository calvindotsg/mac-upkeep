"""Tests for task execution."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from maintenance.config import Config, TaskDef
from maintenance.output import Output
from maintenance.tasks import (
    _build_cmd,
    _run,
    _should_run,
    _update_last_run,
    run_all_tasks,
    run_task,
    strip_ansi,
)


def test_strip_ansi_removes_color_codes():
    assert strip_ansi("\x1b[0;32m✓\x1b[0m test") == "✓ test"


def test_strip_ansi_preserves_plain_text():
    assert strip_ansi("no colors here") == "no colors here"


# --- _build_cmd ---


def test_build_cmd_simple():
    td = TaskDef(name="test", description="", command="brew update", detect="brew")
    assert _build_cmd(td) == ["brew", "update"]


def test_build_cmd_shell():
    td = TaskDef(
        name="test",
        description="",
        command="fisher update",
        detect="fish",
        shell="fish --interactive -c",
    )
    assert _build_cmd(td) == ["fish", "--interactive", "-c", "fisher update"]


def test_build_cmd_sudo():
    td = TaskDef(
        name="test",
        description="",
        command="/opt/homebrew/bin/mo clean",
        detect="/opt/homebrew/bin/mo",
        sudo=True,
    )
    assert _build_cmd(td) == ["sudo", "-n", "/opt/homebrew/bin/mo", "clean"]


def test_build_cmd_shell_plus_sudo():
    """sudo and shell are composable — sudo wraps the shell invocation."""
    td = TaskDef(
        name="test",
        description="",
        command="some_cmd",
        shell="fish -c",
        sudo=True,
    )
    assert _build_cmd(td) == ["sudo", "-n", "fish", "-c", "some_cmd"]


def test_build_cmd_shlex_handles_quotes():
    td = TaskDef(
        name="test",
        description="",
        command="brew bundle cleanup --force --file=/path/to/Brewfile",
    )
    assert _build_cmd(td) == ["brew", "bundle", "cleanup", "--force", "--file=/path/to/Brewfile"]


# --- run_task ---


def test_run_task_skips_disabled():
    config = Config.load()
    # Disable gcloud via task_defs
    config.task_defs["gcloud"].enabled = False
    result = run_task("gcloud", ["gcloud", "update"], config=config, detect="gcloud")
    assert result.status == "skipped"
    assert result.reason == "disabled"


@patch("maintenance.tasks.shutil.which", return_value=None)
def test_run_task_skips_missing_command(mock_which):
    config = Config.load()
    result = run_task("gcloud", ["gcloud", "update"], config=config, detect="gcloud")
    assert result.status == "skipped"
    assert result.reason == "not installed"


def test_run_task_dry_run_does_not_execute():
    config = Config.load()
    with patch("maintenance.tasks.shutil.which", return_value="/usr/bin/gcloud"):
        result = run_task(
            "gcloud", ["gcloud", "update"], config=config, dry_run=True, detect="gcloud"
        )
    assert result.status == "ok"
    assert result.reason == "dry-run"


@patch("maintenance.tasks.subprocess.run")
@patch("maintenance.tasks.shutil.which", return_value="/usr/bin/echo")
def test_run_task_executes_command(mock_which, mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
    config = Config.load()
    result = run_task("gcloud", ["echo", "test"], config=config, detect="echo")
    assert result.status == "ok"
    assert result.reason == ""
    assert result.duration > 0
    # Last call should be the task command (first may be get_brew_prefix)
    task_call = mock_run.call_args_list[-1]
    assert task_call[0][0] == ["echo", "test"]
    assert task_call[1]["stdin"] == subprocess.DEVNULL


@patch("maintenance.tasks.subprocess.run")
@patch("maintenance.tasks.shutil.which", return_value="/usr/bin/echo")
def test_run_task_nonzero_exit_returns_failed(mock_which, mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
    config = Config.load()
    result = run_task("gcloud", ["echo", "test"], config=config, detect="echo")
    assert result.status == "failed"
    assert result.reason == "exit code 1"
    assert result.duration > 0


@patch("maintenance.tasks.subprocess.run")
@patch("maintenance.tasks.shutil.which", return_value="/usr/bin/echo")
def test_run_task_timeout_returns_failed(mock_which, mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="echo", timeout=300)
    config = Config.load()
    result = run_task("gcloud", ["echo", "test"], config=config, detect="echo")
    assert result.status == "failed"
    assert result.reason == "timed out"


@patch("maintenance.tasks.subprocess.run")
@patch("maintenance.tasks.shutil.which", return_value="/usr/bin/echo")
def test_run_task_uses_custom_timeout(mock_which, mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    config = Config.load()
    run_task("gcloud", ["echo", "test"], config=config, detect="echo", timeout=600)
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs["timeout"] == 600


def test_run_task_detect_fallback_to_cmd0():
    """When detect is empty, falls back to cmd[0]."""
    config = Config.load()
    with patch("maintenance.tasks.shutil.which", return_value=None) as mock_which:
        result = run_task("test", ["nonexistent_binary"], config=config, detect="")
    mock_which.assert_called_with("nonexistent_binary")
    assert result.status == "skipped"
    assert result.reason == "not installed"


# --- Frequency scheduling tests ---


def test_should_run_never_ran(tmp_path, monkeypatch):
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", tmp_path / "last-run.json")
    config = Config.load()
    assert _should_run("gcloud", config) is True


def test_should_run_within_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert _should_run("gcloud", config) is False


def test_should_run_past_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    # gcloud is monthly (threshold 27 days), so 30 days old should trigger
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": old}))
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert _should_run("gcloud", config) is True


def test_should_run_monthly_within_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=15)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert _should_run("gcloud", config) is False


def test_should_run_corrupt_timestamp(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    state_file.write_text(json.dumps({"gcloud": "not-a-date"}))
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert _should_run("gcloud", config) is True


def test_should_run_corrupt_json(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    state_file.write_text("{invalid json")
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config.load()
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
    config = Config.load()
    output = Output(interactive=False)
    result = _run(
        "gcloud",
        ["gcloud", "update"],
        config=config,
        output=output,
        dry_run=False,
        detect="gcloud",
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
    config = Config.load()
    output = Output(interactive=False)
    result = _run(
        "gcloud",
        ["gcloud", "update"],
        config=config,
        output=output,
        dry_run=False,
        force_tasks={"gcloud"},
        detect="gcloud",
    )
    assert result.status == "ok"


def test_force_filters_unselected_tasks():
    """_run() skips tasks not in force_tasks with 'not selected'."""
    config = Config.load()
    output = Output(interactive=False)
    result = _run(
        "pnpm",
        ["pnpm", "store", "prune"],
        config=config,
        output=output,
        dry_run=False,
        force_tasks={"gcloud"},
        detect="pnpm",
    )
    assert result.status == "skipped"
    assert result.reason == "not selected"


def test_dry_run_no_timestamp_update(tmp_path, monkeypatch):
    """Dry-run does not update the last-run timestamp."""
    state_dir = tmp_path / "maintenance"
    state_file = state_dir / "last-run.json"
    monkeypatch.setattr("maintenance.tasks._STATE_DIR", state_dir)
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    config = Config.load()
    output = Output(interactive=False)
    with patch("maintenance.tasks.shutil.which", return_value="/usr/bin/gcloud"):
        result = _run(
            "gcloud",
            ["gcloud", "update"],
            config=config,
            output=output,
            dry_run=True,
            detect="gcloud",
        )
    assert result.status == "ok"
    assert result.reason == "dry-run"
    assert not state_file.exists()


# --- run_all_tasks require_file tests ---


def test_run_all_tasks_require_file_skips_missing(tmp_path):
    """run_all_tasks skips tasks with require_file pointing to nonexistent file."""
    config = Config.load()
    # brew_bundle has require_file="${BREWFILE}" which resolves to a real path
    # Override it to point to a nonexistent file
    config.task_defs["brew_bundle"].require_file = str(tmp_path / "nonexistent")
    # Only run brew_bundle to isolate the test
    config.run_order = ["brew_bundle"]
    output = Output(interactive=False)
    results = run_all_tasks(
        config=config, output=output, dry_run=False, force_tasks={"brew_bundle"}
    )
    assert len(results) == 1
    assert results[0].status == "skipped"
    assert "file not found" in results[0].reason


def test_run_all_tasks_require_file_filter_before_file_check():
    """require_file tasks show 'not selected' when filtered, not 'file not found'."""
    config = Config.load()
    config.task_defs["brew_bundle"].require_file = "/nonexistent"
    config.run_order = ["brew_bundle"]
    output = Output(interactive=False)
    # force_tasks does NOT include brew_bundle → filter wins
    results = run_all_tasks(config=config, output=output, dry_run=False, force_tasks={"gcloud"})
    assert len(results) == 1
    assert results[0].reason == "not selected"


@patch("maintenance.tasks.subprocess.run")
@patch("maintenance.tasks.shutil.which", return_value="/usr/bin/echo")
def test_failed_task_no_timestamp_update(mock_which, mock_run, tmp_path, monkeypatch):
    """Failed task does not update the last-run timestamp."""
    state_dir = tmp_path / "maintenance"
    state_file = state_dir / "last-run.json"
    monkeypatch.setattr("maintenance.tasks._STATE_DIR", state_dir)
    monkeypatch.setattr("maintenance.tasks._STATE_FILE", state_file)
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
    config = Config.load()
    output = Output(interactive=False)
    result = _run(
        "gcloud",
        ["echo", "test"],
        config=config,
        output=output,
        dry_run=False,
        detect="echo",
    )
    assert result.status == "failed"
    assert not state_file.exists()
