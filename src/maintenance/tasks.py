"""Maintenance task definitions and execution."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from maintenance.config import Config
from maintenance.output import TaskResult

if TYPE_CHECKING:
    from maintenance.output import Output

logger = logging.getLogger("maintenance")

TASKS: dict[str, str] = {
    "brew_update": "Update Homebrew package database",
    "brew_upgrade": "Upgrade outdated formulae and casks",
    "gcloud": "Update Google Cloud SDK components",
    "pnpm": "Prune pnpm content-addressable store",
    "uv": "Prune uv package cache",
    "fisher": "Update Fish shell plugins",
    "mo_clean": "Clean system and user caches (sudo)",
    "mo_optimize": "Optimize DNS, Spotlight, fonts, Dock (sudo)",
    "mo_purge": "Remove old project artifacts",
    "brew_cleanup": "Remove old versions and cache files",
    "brew_bundle": "Remove packages not in Brewfile",
}

ALL_TASK_NAMES = list(TASKS)

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


def get_brew_prefix() -> str:
    """Detect Homebrew prefix (portable: Apple Silicon /opt/homebrew, Intel /usr/local)."""
    brew = shutil.which("brew")
    if brew:
        try:
            result = subprocess.run([brew, "--prefix"], capture_output=True, text=True, timeout=5)
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass
    return "/opt/homebrew" if os.uname().machine == "arm64" else "/usr/local"


def strip_ansi(text: str) -> str:
    """Remove ANSI color codes from text."""
    return ANSI_PATTERN.sub("", text)


def run_task(
    name: str,
    cmd: list[str],
    *,
    config: Config,
    output: Output | None = None,
    dry_run: bool = False,
    needs_sudo: bool = False,
) -> TaskResult:
    """Execute a maintenance task with auto-detection and graceful failure.

    All logging/display is delegated to Output. run_task is a pure executor.
    """
    task_key = name.lower().replace(" ", "_")

    if not config.is_enabled(task_key):
        return TaskResult(name, "skipped", reason="disabled")

    # Check if the primary command exists
    primary_cmd = cmd[0] if not needs_sudo else cmd[2]  # skip sudo -n
    if not shutil.which(primary_cmd):
        return TaskResult(name, "skipped", reason="not installed")

    if dry_run:
        return TaskResult(name, "ok", reason="dry-run")

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
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
    needs_sudo: bool = False,
    force_tasks: set[str] | None = None,
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

    primary_cmd = cmd[0] if not needs_sudo else cmd[2]
    will_run = config.is_enabled(task_key) and shutil.which(primary_cmd) is not None
    if will_run:
        output.task_start(name)
    result = run_task(
        name, cmd, config=config, output=output, dry_run=dry_run, needs_sudo=needs_sudo
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
    brew_prefix = get_brew_prefix()
    mo_bin = f"{brew_prefix}/bin/mo"
    results: list[TaskResult] = []

    tasks = [
        ("brew_update", ["brew", "update"]),
        ("brew_upgrade", ["brew", "upgrade"]),
        ("gcloud", ["gcloud", "components", "update", "--quiet"]),
        ("pnpm", ["pnpm", "store", "prune"]),
        ("uv", ["uv", "cache", "prune", "--force"]),
        # --interactive: fisher needs job control (jorgebucaran/fisher#608)
        ("fisher", ["fish", "--interactive", "-c", "fisher update"]),
    ]

    for name, cmd in tasks:
        results.append(
            _run(name, cmd, config=config, output=output, dry_run=dry_run, force_tasks=force_tasks)
        )

    # sudo tasks: env_keep in sudoers preserves HOME (not /var/root)
    sudo_tasks = [
        ("mo_clean", [mo_bin, "clean"]),
        ("mo_optimize", [mo_bin, "optimize"]),
    ]
    for name, cmd in sudo_tasks:
        results.append(
            _run(
                name,
                ["sudo", "-n"] + cmd,
                config=config,
                output=output,
                dry_run=dry_run,
                needs_sudo=True,
                force_tasks=force_tasks,
            )
        )

    # mo purge: no sudo, permanent rm -rf, no age threshold
    results.append(
        _run(
            "mo_purge",
            [mo_bin, "purge"],
            config=config,
            output=output,
            dry_run=dry_run,
            force_tasks=force_tasks,
        )
    )

    # brew cleanup: after mo_clean's autoremove, before brew_bundle
    results.append(
        _run(
            "brew_cleanup",
            ["brew", "cleanup", "--prune=all"],
            config=config,
            output=output,
            dry_run=dry_run,
            force_tasks=force_tasks,
        )
    )

    # brew bundle cleanup: runs last (homebrew/brew#21350)
    task_key = "brew_bundle"
    if force_tasks is not None and task_key not in force_tasks:
        r = TaskResult("brew_bundle", "skipped", reason="not selected")
        output.task_done(r)
        results.append(r)
    elif not config.is_enabled("brew_bundle"):
        r = TaskResult("brew_bundle", "skipped", reason="disabled")
        output.task_done(r)
        results.append(r)
    elif not config.brewfile or not Path(config.brewfile).is_file():
        r = TaskResult("brew_bundle", "skipped", reason="no Brewfile found")
        output.task_done(r)
        results.append(r)
    else:
        results.append(
            _run(
                "brew_bundle",
                ["brew", "bundle", "cleanup", "--force", f"--file={config.brewfile}"],
                config=config,
                output=output,
                dry_run=dry_run,
                force_tasks=force_tasks,
            )
        )

    return results
