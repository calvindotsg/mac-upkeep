"""Tests for task execution."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from mac_upkeep.config import Config, TaskDef
from mac_upkeep.output import Output, TaskResult
from mac_upkeep.tasks import (
    HANDLERS,
    KNOWN_HANDLERS,
    _build_cmd,
    _run,
    _should_run,
    _update_last_run,
    format_last_run,
    format_next_run,
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


@patch("mac_upkeep.tasks.shutil.which", return_value=None)
def test_run_task_skips_missing_command(mock_which):
    config = Config.load()
    result = run_task("gcloud", ["gcloud", "update"], config=config, detect="gcloud")
    assert result.status == "skipped"
    assert result.reason == "not installed"


def test_run_task_dry_run_does_not_execute():
    config = Config.load()
    with patch("mac_upkeep.tasks.shutil.which", return_value="/usr/bin/gcloud"):
        result = run_task(
            "gcloud", ["gcloud", "update"], config=config, dry_run=True, detect="gcloud"
        )
    assert result.status == "ok"
    assert result.reason == "dry-run"


@patch("mac_upkeep.tasks.subprocess.run")
@patch("mac_upkeep.tasks.shutil.which", return_value="/usr/bin/echo")
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


@patch("mac_upkeep.tasks.subprocess.run")
@patch("mac_upkeep.tasks.shutil.which", return_value="/usr/bin/echo")
def test_run_task_nonzero_exit_returns_failed(mock_which, mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
    config = Config.load()
    result = run_task("gcloud", ["echo", "test"], config=config, detect="echo")
    assert result.status == "failed"
    assert result.reason == "exit code 1"
    assert result.duration > 0


@patch("mac_upkeep.tasks.subprocess.run")
@patch("mac_upkeep.tasks.shutil.which", return_value="/usr/bin/echo")
def test_run_task_timeout_returns_failed(mock_which, mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="echo", timeout=300)
    config = Config.load()
    result = run_task("gcloud", ["echo", "test"], config=config, detect="echo")
    assert result.status == "failed"
    assert result.reason == "timed out"


@patch("mac_upkeep.tasks.subprocess.run")
@patch("mac_upkeep.tasks.shutil.which", return_value="/usr/bin/echo")
def test_run_task_uses_custom_timeout(mock_which, mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    config = Config.load()
    run_task("gcloud", ["echo", "test"], config=config, detect="echo", timeout=600)
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs["timeout"] == 600


def test_run_task_detect_fallback_to_cmd0():
    """When detect is empty, falls back to cmd[0]."""
    config = Config.load()
    with patch("mac_upkeep.tasks.shutil.which", return_value=None) as mock_which:
        result = run_task("test", ["nonexistent_binary"], config=config, detect="")
    mock_which.assert_called_with("nonexistent_binary")
    assert result.status == "skipped"
    assert result.reason == "not installed"


# --- Frequency scheduling tests ---


def test_should_run_never_ran(tmp_path, monkeypatch):
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", tmp_path / "last-run.json")
    config = Config.load()
    assert _should_run("gcloud", config) is True


def test_should_run_within_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert _should_run("gcloud", config) is False


def test_should_run_past_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    # gcloud is monthly (threshold 27 days), so 30 days old should trigger
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": old}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert _should_run("gcloud", config) is True


def test_should_run_monthly_within_threshold(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=15)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert _should_run("gcloud", config) is False


def test_should_run_corrupt_timestamp(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    state_file.write_text(json.dumps({"gcloud": "not-a-date"}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert _should_run("gcloud", config) is True


def test_should_run_corrupt_json(tmp_path, monkeypatch):
    state_file = tmp_path / "last-run.json"
    state_file.write_text("{invalid json")
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert _should_run("gcloud", config) is True


def test_update_last_run_creates_state_file(tmp_path, monkeypatch):
    state_dir = tmp_path / "mac-upkeep"
    state_file = state_dir / "last-run.json"
    monkeypatch.setattr("mac_upkeep.tasks._STATE_DIR", state_dir)
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    _update_last_run("gcloud")
    assert state_file.is_file()
    state = json.loads(state_file.read_text())
    assert "gcloud" in state


def test_run_frequency_skip(tmp_path, monkeypatch):
    """_run() skips task with 'ran recently' when frequency check applies (no --force)."""
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
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
    assert result.reason.startswith("ran recently, next")


@patch("mac_upkeep.tasks.subprocess.run")
@patch("mac_upkeep.tasks.shutil.which", return_value="/usr/bin/gcloud")
def test_force_bypasses_frequency(mock_which, mock_run, tmp_path, monkeypatch):
    """_run() executes forced task (filter passes, frequency bypassed)."""
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    monkeypatch.setattr("mac_upkeep.tasks._STATE_DIR", tmp_path)
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
    state_dir = tmp_path / "mac-upkeep"
    state_file = state_dir / "last-run.json"
    monkeypatch.setattr("mac_upkeep.tasks._STATE_DIR", state_dir)
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    config = Config.load()
    output = Output(interactive=False)
    with patch("mac_upkeep.tasks.shutil.which", return_value="/usr/bin/gcloud"):
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


@patch("mac_upkeep.tasks.subprocess.run")
@patch("mac_upkeep.tasks.shutil.which", return_value="/usr/bin/echo")
def test_failed_task_no_timestamp_update(mock_which, mock_run, tmp_path, monkeypatch):
    """Failed task does not update the last-run timestamp."""
    state_dir = tmp_path / "mac-upkeep"
    state_file = state_dir / "last-run.json"
    monkeypatch.setattr("mac_upkeep.tasks._STATE_DIR", state_dir)
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
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


# --- format_last_run ---


def test_format_last_run_none():
    assert format_last_run(None) == "never"


def test_format_last_run_corrupt():
    assert format_last_run("not-a-date") == "never"


def test_format_last_run_just_now():
    ts = datetime.now().isoformat(timespec="seconds")
    assert format_last_run(ts) == "just now"


def test_format_last_run_hours_ago():
    ts = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
    assert format_last_run(ts) == "3h ago"


def test_format_last_run_one_hour_ago():
    ts = (datetime.now() - timedelta(hours=1, minutes=5)).isoformat(timespec="seconds")
    assert format_last_run(ts) == "1 hour ago"


def test_format_last_run_one_day_ago():
    ts = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    assert format_last_run(ts) == "1 day ago"


def test_format_last_run_many_days_ago():
    ts = (datetime.now() - timedelta(days=5)).isoformat(timespec="seconds")
    assert format_last_run(ts) == "5 days ago"


# --- format_next_run ---


def test_format_next_run_never_ran(tmp_path, monkeypatch):
    """Never ran → immediately eligible → 'now'."""
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", tmp_path / "last-run.json")
    config = Config.load()
    assert format_next_run("gcloud", config) == "now"


def test_format_next_run_within_threshold(tmp_path, monkeypatch):
    """gcloud is monthly (27-day threshold), ran 1 day ago → 26 days remaining."""
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": recent}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert format_next_run("gcloud", config) == "in 26 days"


def test_format_next_run_past_threshold(tmp_path, monkeypatch):
    """gcloud ran 30 days ago (past 27-day threshold) → 'now'."""
    state_file = tmp_path / "last-run.json"
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"gcloud": old}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    config = Config.load()
    assert format_next_run("gcloud", config) == "now"


def test_format_next_run_with_state_param(tmp_path, monkeypatch):
    """Pre-loaded state dict bypasses file read and produces same result."""
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", tmp_path / "last-run.json")
    config = Config.load()
    recent = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    state = {"gcloud": recent}
    assert format_next_run("gcloud", config, state) == "in 26 days"


# --- daily frequency ---


def test_should_run_daily_blocks_within_20h(tmp_path, monkeypatch):
    """Daily task that ran 10h ago must be skipped (threshold 20h)."""
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(hours=10)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"git_sync": recent}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    config = Config.load()
    config.task_defs["git_sync"] = TaskDef(
        name="git_sync", description="", command="git", frequency="daily"
    )
    assert _should_run("git_sync", config) is False


def test_should_run_daily_allows_after_20h(tmp_path, monkeypatch):
    """Daily task that ran 21h ago is eligible again."""
    state_file = tmp_path / "last-run.json"
    old = (datetime.now() - timedelta(hours=21)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"git_sync": old}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)
    config = Config.load()
    config.task_defs["git_sync"] = TaskDef(
        name="git_sync", description="", command="git", frequency="daily"
    )
    assert _should_run("git_sync", config) is True


def test_format_next_run_daily_hour_scale(tmp_path, monkeypatch):
    """Daily task ran ~2h30m ago → ~17h30m remaining → 'in 17h'."""
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", tmp_path / "last-run.json")
    config = Config.load()
    config.task_defs["git_sync"] = TaskDef(
        name="git_sync", description="", command="git", frequency="daily"
    )
    recent = (datetime.now() - timedelta(hours=2, minutes=30)).isoformat(timespec="seconds")
    assert format_next_run("git_sync", config, {"git_sync": recent}) == "in 17h"


def test_format_next_run_daily_one_hour(tmp_path, monkeypatch):
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", tmp_path / "last-run.json")
    config = Config.load()
    config.task_defs["git_sync"] = TaskDef(
        name="git_sync", description="", command="git", frequency="daily"
    )
    recent = (datetime.now() - timedelta(hours=18, minutes=45)).isoformat(timespec="seconds")
    assert format_next_run("git_sync", config, {"git_sync": recent}) == "in 1 hour"


def test_format_next_run_daily_under_1h(tmp_path, monkeypatch):
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", tmp_path / "last-run.json")
    config = Config.load()
    config.task_defs["git_sync"] = TaskDef(
        name="git_sync", description="", command="git", frequency="daily"
    )
    recent = (datetime.now() - timedelta(hours=19, minutes=30)).isoformat(timespec="seconds")
    assert format_next_run("git_sync", config, {"git_sync": recent}) == "in <1h"


# --- handler dispatch ---


def _stub_handler(name="stub_task", status="ok", reason="stub ran"):
    """Factory for a handler that returns a known TaskResult and records calls."""
    calls = []

    def handler(config, output, dry_run):
        calls.append({"dry_run": dry_run})
        return TaskResult(name, status, reason=reason)

    return handler, calls


def test_handler_dispatch_runs_and_records_state(tmp_path, monkeypatch):
    """run_all_tasks invokes the registered handler and updates last-run state."""
    state_file = tmp_path / "last-run.json"
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)

    handler, calls = _stub_handler()
    monkeypatch.setitem(HANDLERS, "stub", handler)
    monkeypatch.setattr("mac_upkeep.tasks.KNOWN_HANDLERS", {"stub", "git_sync"})

    config = Config.load()
    config.task_defs["stub_task"] = TaskDef(
        name="stub_task",
        description="",
        command="",
        detect="",
        handler="stub",
        frequency="daily",
    )
    config.run_order = ["stub_task"]

    output = MagicMock(spec=Output)
    results = run_all_tasks(config=config, output=output, dry_run=False)

    assert len(calls) == 1
    assert results[0].status == "ok"
    state = json.loads(state_file.read_text())
    assert "stub_task" in state


def test_handler_dispatch_frequency_gate_skips(tmp_path, monkeypatch):
    """Recent timestamp in state → handler not invoked."""
    state_file = tmp_path / "last-run.json"
    recent = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    state_file.write_text(json.dumps({"stub_task": recent}))
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", state_file)

    handler, calls = _stub_handler()
    monkeypatch.setitem(HANDLERS, "stub", handler)
    monkeypatch.setattr("mac_upkeep.tasks.KNOWN_HANDLERS", {"stub", "git_sync"})

    config = Config.load()
    config.task_defs["stub_task"] = TaskDef(
        name="stub_task",
        description="",
        command="",
        detect="",
        handler="stub",
        frequency="daily",
    )
    config.run_order = ["stub_task"]

    output = MagicMock(spec=Output)
    results = run_all_tasks(config=config, output=output, dry_run=False)

    assert calls == []
    assert results[0].status == "skipped"
    assert "ran recently" in results[0].reason


def test_handler_dispatch_force_filter(tmp_path, monkeypatch):
    """force_tasks without the handler's key skips it without invoking."""
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", tmp_path / "last-run.json")

    handler, calls = _stub_handler()
    monkeypatch.setitem(HANDLERS, "stub", handler)
    monkeypatch.setattr("mac_upkeep.tasks.KNOWN_HANDLERS", {"stub", "git_sync"})

    config = Config.load()
    config.task_defs["stub_task"] = TaskDef(
        name="stub_task",
        description="",
        command="",
        detect="",
        handler="stub",
    )
    config.run_order = ["stub_task"]

    output = MagicMock(spec=Output)
    results = run_all_tasks(config=config, output=output, dry_run=False, force_tasks={"other_task"})

    assert calls == []
    assert results[0].status == "skipped"
    assert results[0].reason == "not selected"


def test_handler_dispatch_detect_miss(tmp_path, monkeypatch):
    """detect binary missing → skipped 'not installed', handler not invoked."""
    monkeypatch.setattr("mac_upkeep.tasks._STATE_FILE", tmp_path / "last-run.json")

    handler, calls = _stub_handler()
    monkeypatch.setitem(HANDLERS, "stub", handler)
    monkeypatch.setattr("mac_upkeep.tasks.KNOWN_HANDLERS", {"stub", "git_sync"})
    monkeypatch.setattr("mac_upkeep.tasks.shutil.which", lambda _: None)

    config = Config.load()
    config.task_defs["stub_task"] = TaskDef(
        name="stub_task",
        description="",
        command="",
        detect="definitely-not-a-real-binary",
        handler="stub",
    )
    config.run_order = ["stub_task"]

    output = MagicMock(spec=Output)
    results = run_all_tasks(config=config, output=output, dry_run=False)

    assert calls == []
    assert results[0].status == "skipped"
    assert results[0].reason == "not installed"


def test_known_handlers_kept_in_sync():
    """KNOWN_HANDLERS starts empty (no handlers registered in Phase 3)."""
    assert KNOWN_HANDLERS == set(HANDLERS.keys())
