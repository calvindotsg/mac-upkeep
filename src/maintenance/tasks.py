"""Maintenance task definitions and execution."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from maintenance.config import Config, TaskDef, load_default_task_names
from maintenance.output import TaskResult

if TYPE_CHECKING:
    from maintenance.output import Output

logger = logging.getLogger("maintenance")

# Load task names from bundled defaults.toml at import time (for shell completion)
TASKS, _DEFAULT_ORDER = load_default_task_names()
ALL_TASK_NAMES = list(_DEFAULT_ORDER)

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")

# State file for per-task frequency tracking
_xdg_state = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
_STATE_DIR = Path(_xdg_state) / "maintenance"
_STATE_FILE = _STATE_DIR / "last-run.json"
FREQUENCY_THRESHOLDS = {"weekly": 6, "monthly": 27}  # days (buffer for schedule drift)


def _load_state() -> dict[str, str]:
    """Load last-run timestamps. Returns empty dict on missing/corrupt file."""
    try:
        return json.loads(_STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    """Write state file."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def _should_run(task_key: str, config: Config) -> bool:
    """Check if enough time has elapsed since last run for this task's frequency."""
    state = _load_state()
    last_run_str = state.get(task_key)
    if not last_run_str:
        return True
    try:
        last_run = datetime.fromisoformat(last_run_str)
    except ValueError:
        return True
    threshold_days = FREQUENCY_THRESHOLDS.get(config.get_frequency(task_key), 6)
    return (datetime.now() - last_run).days >= threshold_days


def _update_last_run(task_key: str) -> None:
    """Record successful task completion timestamp."""
    state = _load_state()
    state[task_key] = datetime.now().isoformat(timespec="seconds")
    _save_state(state)


def strip_ansi(text: str) -> str:
    """Remove ANSI color codes from text."""
    return ANSI_PATTERN.sub("", text)


def _build_cmd(td: TaskDef) -> list[str]:
    """Convert a TaskDef into a subprocess command list.

    Shell and sudo are composable: both branches merge before the sudo check.
    """
    if td.shell:
        # e.g., "fish --interactive -c" + "fisher update"
        # → ["fish", "--interactive", "-c", "fisher update"]
        cmd = td.shell.split() + [td.command]
    else:
        cmd = shlex.split(td.command)
    if td.sudo:
        cmd = ["sudo", "-n"] + cmd
    return cmd


def run_task(
    name: str,
    cmd: list[str],
    *,
    config: Config,
    output: Output | None = None,
    dry_run: bool = False,
    detect: str = "",
    timeout: int = 300,
) -> TaskResult:
    """Execute a maintenance task with auto-detection and graceful failure.

    All logging/display is delegated to Output. run_task is a pure executor.
    """
    task_key = name.lower().replace(" ", "_")

    if not config.is_enabled(task_key):
        return TaskResult(name, "skipped", reason="disabled")

    # Check if the primary command exists
    detect_cmd = detect or cmd[0]
    if not shutil.which(detect_cmd):
        return TaskResult(name, "skipped", reason="not installed")

    if dry_run:
        return TaskResult(name, "ok", reason="dry-run")

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        duration = time.monotonic() - start
        raw_output = strip_ansi(result.stdout + result.stderr).strip()
        if raw_output:
            for line in raw_output.splitlines():
                if output is not None:
                    output.task_debug(line)
                else:
                    logger.debug("  %s", line)
        if result.returncode != 0:
            return TaskResult(
                name, "failed", reason=f"exit code {result.returncode}", duration=duration
            )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return TaskResult(name, "failed", reason="timed out", duration=duration)
    except subprocess.CalledProcessError as e:
        duration = time.monotonic() - start
        return TaskResult(name, "failed", reason=strip_ansi(e.stderr or str(e)), duration=duration)

    return TaskResult(name, "ok", duration=duration)


def _run(
    name: str,
    cmd: list[str],
    *,
    config: Config,
    output: Output,
    dry_run: bool,
    force_tasks: set[str] | None = None,
    detect: str = "",
    timeout: int = 300,
) -> TaskResult:
    """Run one task: start → execute → done.

    --force filters to selected tasks. Frequency applies by default;
    forced tasks bypass it. task_start only called for tasks that execute.
    """
    task_key = name.lower().replace(" ", "_")

    # --force filters to specific tasks
    if force_tasks is not None and task_key not in force_tasks:
        result = TaskResult(name, "skipped", reason="not selected")
        output.task_done(result)
        return result

    # Frequency: skip if ran recently (no --force = frequency applies)
    if not dry_run and force_tasks is None and not _should_run(task_key, config):
        result = TaskResult(name, "skipped", reason="ran recently")
        output.task_done(result)
        return result

    detect_cmd = detect or cmd[0]
    will_run = config.is_enabled(task_key) and shutil.which(detect_cmd) is not None
    if will_run:
        output.task_start(name)
    result = run_task(
        name,
        cmd,
        config=config,
        output=output,
        dry_run=dry_run,
        detect=detect,
        timeout=timeout,
    )
    output.task_done(result)

    # Record timestamp on success (not dry-run)
    if result.status == "ok" and result.reason != "dry-run":
        _update_last_run(task_key)

    return result


def run_all_tasks(
    *,
    config: Config,
    output: Output,
    dry_run: bool = False,
    force_tasks: set[str] | None = None,
) -> list[TaskResult]:
    """Run all maintenance tasks in order. Returns list of task results."""
    results: list[TaskResult] = []

    for task_name in config.run_order:
        td = config.task_defs.get(task_name)
        if td is None:
            continue

        # require_file tasks: check filter → enabled → file exists
        # (preserves current brew_bundle delegation pattern)
        if td.require_file and not Path(td.require_file).is_file():
            if force_tasks is not None and task_name not in force_tasks:
                r = TaskResult(task_name, "skipped", reason="not selected")
            elif not td.enabled:
                r = TaskResult(task_name, "skipped", reason="disabled")
            else:
                r = TaskResult(task_name, "skipped", reason=f"file not found: {td.require_file}")
            output.task_done(r)
            results.append(r)
            continue

        cmd = _build_cmd(td)
        results.append(
            _run(
                task_name,
                cmd,
                config=config,
                output=output,
                dry_run=dry_run,
                force_tasks=force_tasks,
                detect=td.detect,
                timeout=td.timeout,
            )
        )

    return results
