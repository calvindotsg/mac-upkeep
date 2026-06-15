"""Built-in editor_cache handler: reclaim Electron/editor caches mole can't see.

Apps like Notion (Electron) and Zed leave multi-GB caches under names that
mole's risk classifier ignores ("Service Worker", "node/cache"), so they are
never cleaned by `mo clean`. This handler targets those paths directly, but
only when the owning app is closed (deleting a running Electron app's cache can
corrupt its state) and, optionally, only above a size threshold.

Safety mirrors tw93/mole: surgical sub-folder targeting (e.g. the
`Service Worker/CacheStorage` bloat, never the sibling `Database` that holds the
service-worker registrations), a `pgrep -x` running-app guard (robust under
launchd, unlike osascript), and a path guard that refuses anything not strictly
under ~/Library/Application Support.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from mac_upkeep.output import TaskResult

if TYPE_CHECKING:
    from mac_upkeep.config import Config
    from mac_upkeep.output import Output

# Built-in targets. Each app is cleaned only when its `process` is not running.
# `min_size_mb` gates a target: 0 = always clean when closed; >0 = only when the
# target exceeds the threshold (Zed re-downloads LSP tarballs, so small caches
# aren't worth the re-fetch). Paths use ~ and are expanded at run time.
DEFAULT_APPS: list[dict] = [
    {
        "name": "Notion",
        "process": "Notion",
        "min_size_mb": 0,
        "targets": [
            "~/Library/Application Support/Notion/Partitions/notion/Service Worker/CacheStorage",
            "~/Library/Application Support/Notion/Partitions/notion/Cache",
            "~/Library/Application Support/Notion/Partitions/notion/Code Cache",
            "~/Library/Application Support/Notion/Partitions/notion/GPUCache",
        ],
    },
    {
        "name": "Zed",
        "process": "zed",
        "min_size_mb": 2048,
        "targets": [
            "~/Library/Application Support/Zed/node/cache",
        ],
    },
]

# Deletion is refused for anything not strictly below this directory.
_SAFE_ROOT = Path.home() / "Library" / "Application Support"


def _dir_size(path: Path) -> int:
    """Total bytes under path, following no symlinks. 0 if unreadable."""
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            fp = Path(root) / name
            try:
                st = fp.lstat()
            except OSError:
                continue
            # Skip symlinks: count only real bytes that deletion reclaims.
            if not os.path.islink(fp):
                total += st.st_size
    return total


def _pgrep_running(process: str) -> bool:
    """True if a process named exactly `process` appears to be running (pgrep -x).

    Fail-closed: any uncertainty returns True so the caller skips deletion. An
    empty/missing process name, a pgrep that errors (exit >=2 = bad args /
    internal error, distinct from exit 1 = no match), or an OS/timeout error all
    count as "assume running, don't delete".
    """
    if not process:
        return True
    try:
        result = subprocess.run(
            ["pgrep", "-x", process],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    # pgrep exit codes: 0 = match (running), 1 = no match (not running),
    # >=2 = pgrep itself errored → fail-closed (treat as running).
    return result.returncode != 1


def _is_safe_target(path: Path) -> bool:
    """Guard before any rmtree: refuse anything not safely deep under _SAFE_ROOT.

    Mirrors mole's validate_path_for_deletion: the resolved path must live
    strictly inside ~/Library/Application Support, be at least two segments below
    it (so an app's whole support dir can never be the target), and not be a
    symlink (which could redirect deletion elsewhere).
    """
    if path.is_symlink():
        return False
    try:
        resolved = path.resolve()
        root = _SAFE_ROOT.resolve()
    except OSError:
        return False
    if not resolved.is_relative_to(root):
        return False
    return len(resolved.relative_to(root).parts) >= 2


def _clean_target(target: Path, min_size_mb: int, output: Output, dry_run: bool) -> int:
    """Clean one target dir if eligible. Returns bytes freed (0 if skipped)."""
    if not target.is_dir():
        return 0
    if not _is_safe_target(target):
        output.task_debug(f"  refused unsafe path: {target}")
        return 0
    # Operate on the canonical resolved path so the validated path and the
    # deleted path are identical — closes any symlink-swap TOCTOU between the
    # _is_safe_target check and the rmtree. Re-validate the resolved path.
    try:
        safe = target.resolve(strict=True)
    except OSError:
        return 0
    if not _is_safe_target(safe):
        output.task_debug(f"  refused unsafe path: {target}")
        return 0

    size = _dir_size(safe)
    size_mb = size // (1024 * 1024)
    if size_mb < min_size_mb:
        output.task_debug(f"  {target.name}: {size_mb}MB below {min_size_mb}MB threshold, skipped")
        return 0

    if dry_run:
        output.task_debug(f"  would clean {target.name}: {size_mb}MB")
        return size

    try:
        shutil.rmtree(safe)
    except OSError as exc:
        output.task_debug(f"  failed to clean {target.name}: {exc}")
        raise
    output.task_debug(f"  cleaned {target.name}: {size_mb}MB")
    return size


def run_editor_cache(config: Config, output: Output, dry_run: bool) -> TaskResult:
    """Handler entry point. Clear configured editor caches for closed apps."""
    apps = config.editor_cache_apps or DEFAULT_APPS

    total_freed = 0
    n_cleaned = 0
    n_running = 0
    failures: list[str] = []

    for app in apps:
        name = app.get("name", "?")
        process = app.get("process", "")
        min_size_mb = int(app.get("min_size_mb", 0))
        targets = [Path(os.path.expanduser(t)) for t in app.get("targets", [])]

        if _pgrep_running(process):
            output.task_debug(f"{name}: skipped, app is running")
            n_running += 1
            continue

        app_freed = 0
        try:
            for target in targets:
                app_freed += _clean_target(target, min_size_mb, output, dry_run)
        except OSError:
            failures.append(name)
            continue

        if app_freed:
            n_cleaned += 1
            total_freed += app_freed

    if failures:
        return TaskResult(
            "editor_cache", "failed", reason=f"{len(failures)} failed: {', '.join(failures)}"
        )

    freed_mb = total_freed // (1024 * 1024)
    if dry_run:
        return TaskResult("editor_cache", "ok", reason=f"dry-run: would free {freed_mb}MB")

    parts: list[str] = []
    if n_cleaned:
        parts.append(f"{freed_mb}MB freed")
    if n_running:
        parts.append(f"{n_running} running")
    reason = ", ".join(parts) if parts else "nothing to clean"
    return TaskResult("editor_cache", "ok", reason=reason)
