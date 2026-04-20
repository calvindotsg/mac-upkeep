"""Built-in git_sync handler: fast-forward pulls a user-configured list of repos."""

from __future__ import annotations

import glob
import os
import re
import subprocess
from typing import TYPE_CHECKING

from mac_upkeep.output import TaskResult

if TYPE_CHECKING:
    from mac_upkeep.config import Config
    from mac_upkeep.output import Output

_ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_PATTERN.sub("", text)


def _run_git(path: str, args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def _resolve_paths(patterns: list[str], output: Output) -> list[str]:
    """Expand user paths and globs; emit debug lines for empty matches."""
    paths: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        expanded = os.path.expanduser(pattern)
        if any(ch in expanded for ch in "*?["):
            matches = sorted(glob.glob(expanded))
            if not matches:
                output.task_debug(f"no match: {pattern}")
                continue
            for m in matches:
                if m not in seen:
                    seen.add(m)
                    paths.append(m)
        else:
            if expanded not in seen:
                seen.add(expanded)
                paths.append(expanded)
    return paths


def _sync_repo(path: str, *, skip_dirty: bool) -> tuple[str, str]:
    """Sync one repo. Returns (status, reason) where status is pulled|up-to-date|skipped|failed."""
    r = _run_git(path, ["rev-parse", "--is-inside-work-tree"])
    if r.returncode != 0:
        return "skipped", "not a git repo"

    r = _run_git(path, ["remote"])
    if r.returncode != 0 or not r.stdout.strip():
        return "skipped", "no remote configured"

    branch_r = _run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch_r.stdout.strip() or "?"
    r = _run_git(path, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if r.returncode != 0:
        return "skipped", f"no upstream (branch={branch})"

    if skip_dirty:
        r = _run_git(path, ["status", "--porcelain"])
        if r.stdout.strip():
            return "skipped", "dirty worktree"

    r = _run_git(path, ["pull", "--ff-only"])
    if r.returncode != 0:
        stderr = _strip_ansi(r.stderr).strip().splitlines()
        first = stderr[0] if stderr else f"exit {r.returncode}"
        return "failed", first

    stdout = _strip_ansi(r.stdout).strip().lower()
    if "already up to date" in stdout or "already up-to-date" in stdout:
        return "up-to-date", ""
    return "pulled", ""


def run_git_sync(config: Config, output: Output, dry_run: bool) -> TaskResult:
    """Handler entry point. Aggregate per-repo results into a single TaskResult."""
    patterns = list(config.git_sync_repos)
    if not patterns:
        return TaskResult("git_sync", "skipped", reason="no repos configured")

    paths = _resolve_paths(patterns, output)
    if not paths:
        return TaskResult("git_sync", "skipped", reason="no repos matched")

    if dry_run:
        for path in paths:
            output.task_debug(f"would pull: {path}")
        return TaskResult("git_sync", "ok", reason=f"dry-run: {len(paths)} repos")

    n_pulled = 0
    n_skipped = 0
    failures: list[str] = []
    for path in paths:
        status, reason = _sync_repo(path, skip_dirty=config.git_sync_skip_dirty)
        display = f"{path}: {status}"
        if reason:
            display = f"{display} ({reason})"
        output.task_debug(display)
        if status in ("pulled", "up-to-date"):
            n_pulled += 1
        elif status == "skipped":
            n_skipped += 1
        else:
            failures.append(os.path.basename(path.rstrip("/")))

    if failures:
        names = ", ".join(failures)
        return TaskResult("git_sync", "failed", reason=f"{len(failures)} failed: {names}")

    parts = []
    if n_pulled:
        parts.append(f"{n_pulled} pulled")
    if n_skipped:
        parts.append(f"{n_skipped} skipped")
    reason = ", ".join(parts) if parts else "no repos processed"
    return TaskResult("git_sync", "ok", reason=reason)
