"""Maintenance task definitions and execution."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

from maintenance.config import Config
from maintenance.output import TaskResult

if TYPE_CHECKING:
    from maintenance.output import Output

logger = logging.getLogger("maintenance")

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def get_brew_prefix() -> str:
    """Detect Homebrew prefix (portable: Apple Silicon /opt/homebrew, Intel /usr/local)."""
    brew = shutil.which("brew")
    if brew:
        try:
            result = subprocess.run(
                [brew, "--prefix"], capture_output=True, text=True, timeout=5
            )
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
) -> TaskResult:
    """Run one task with full output lifecycle: start → execute → done.

    task_start is only called for tasks that will actually execute (not for
    immediately-skipped tasks), to avoid logging "Running X..." before a skip.
    """
    task_key = name.lower().replace(" ", "_")
    primary_cmd = cmd[0] if not needs_sudo else cmd[2]
    will_run = (
        config.is_enabled(task_key)
        and shutil.which(primary_cmd) is not None
    )
    if will_run:
        output.task_start(name)
    result = run_task(
        name, cmd, config=config, output=output, dry_run=dry_run, needs_sudo=needs_sudo
    )
    output.task_done(result)
    return result


def run_all_tasks(
    *, config: Config, output: Output, dry_run: bool = False
) -> list[TaskResult]:
    """Run all maintenance tasks in order. Returns list of task results."""
    brew_prefix = get_brew_prefix()
    mo_bin = f"{brew_prefix}/bin/mo"
    results: list[TaskResult] = []

    tasks = [
        ("gcloud", ["gcloud", "components", "update", "--quiet"]),
        ("pnpm", ["pnpm", "store", "prune"]),
        ("uv", ["uv", "cache", "prune"]),
        # --interactive: fisher needs job control (jorgebucaran/fisher#608)
        ("fisher", ["fish", "--interactive", "-c", "fisher update"]),
    ]

    for name, cmd in tasks:
        results.append(_run(name, cmd, config=config, output=output, dry_run=dry_run))

    # sudo tasks: env_keep in sudoers preserves HOME (not /var/root)
    sudo_tasks = [
        ("mo_clean", [mo_bin, "clean"]),
        ("mo_optimize", [mo_bin, "optimize"]),
    ]
    for name, cmd in sudo_tasks:
        results.append(
            _run(
                name, ["sudo", "-n"] + cmd,
                config=config, output=output, dry_run=dry_run, needs_sudo=True,
            )
        )

    # mo purge: no sudo, permanent rm -rf, no age threshold
    results.append(
        _run("mo_purge", [mo_bin, "purge"], config=config, output=output, dry_run=dry_run)
    )

    # brew bundle cleanup: runs last (mo clean runs autoremove — homebrew/brew#21350)
    if config.is_enabled("brew_bundle"):
        if not config.brewfile or not Path(config.brewfile).is_file():
            r = TaskResult("brew_bundle", "skipped", reason="no Brewfile found")
            output.task_done(r)
            results.append(r)
        elif dry_run:
            r = TaskResult("brew_bundle", "ok", reason="dry-run")
            output.task_done(r)
            results.append(r)
        else:
            results.append(
                _run(
                    "brew_bundle",
                    ["brew", "bundle", "cleanup", "--force", f"--file={config.brewfile}"],
                    config=config,
                    output=output,
                    dry_run=False,
                )
            )
    else:
        r = TaskResult("brew_bundle", "skipped", reason="disabled")
        output.task_done(r)
        results.append(r)

    return results
