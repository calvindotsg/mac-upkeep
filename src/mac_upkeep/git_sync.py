"""Built-in git_sync handler: fast-forward pulls a user-configured list of repos."""

from __future__ import annotations

import glob
import os
import subprocess
from typing import TYPE_CHECKING

from mac_upkeep.output import TaskResult, strip_control_sequences

if TYPE_CHECKING:
    from mac_upkeep.config import Config
    from mac_upkeep.output import Output


def _strip_ansi(text: str) -> str:
    """Sanitise a git remote's message before it becomes a user-visible reason."""
    return strip_control_sequences(text)


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_ASKPASS", "/usr/bin/true")
    return env


# Configuration keys a repository can set in its own .git/config that cause git to
# EXECUTE a command. A directory tree that lands under a `repos` glob -- an extracted
# archive, a restored backup, a vendor drop -- is untrusted input, and `safe.directory`
# does not help: it keys on ownership, and anything the user unpacked is owned by the
# user. Every entry below was verified to fire on git 2.55 from the commands this
# handler runs; `git status --porcelain`, the skip_dirty *safety* check, is enough to
# trigger core.fsmonitor and filter drivers on its own.
#
# `credential.helper` and `core.sshCommand` are re-derived from the user's own
# global/system config by _trusted_overrides(), so a repo-local value is dropped
# without breaking an osxkeychain helper or a custom ssh command.
_STATIC_OVERRIDES = [
    "core.fsmonitor=",  # runs on any index refresh, including `status`
    "core.hooksPath=/dev/null",  # .git/hooks: post-merge, reference-transaction
    "protocol.ext.allow=never",  # a repo may re-enable ext:: and get a shell
    "protocol.file.allow=never",  # blocks local-path remote.<n>.uploadpack execution
    # core.gitProxy runs an arbitrary command for git:// remotes and canNOT be
    # neutralised by a `-c` override -- an empty value does not disable it (verified).
    # Denying the transport is the only thing that stops it, and git:// is an
    # unauthenticated, unencrypted legacy protocol no sync target should be using.
    "protocol.git.allow=never",
]

# Multi-valued keys: git accumulates every value it sees, and an empty entry resets
# the accumulated list. Reset first, then append the user's own values back.
_INHERITED_LIST_KEYS = ("credential.helper",)

# Single-valued keys, where an empty value is NOT a reset -- it is a value. Setting
# `core.sshCommand=` makes git try to exec the empty string, which breaks every ssh
# remote. These get the user's own global/system value, or an explicit safe default.
_INHERITED_SCALAR_KEYS = {"core.sshCommand": "ssh"}

_trusted_cache: list[str] | None = None


def _trusted_overrides() -> list[str]:
    """Build the `-c` arguments that neutralise repository-supplied config.

    Computed once per process: two `git config` reads, not two per repository.
    """
    global _trusted_cache
    if _trusted_cache is not None:
        return _trusted_cache

    args: list[str] = []
    for key in _STATIC_OVERRIDES:
        args += ["-c", key]

    for key in _INHERITED_LIST_KEYS:
        args += ["-c", f"{key}="]  # reset the accumulated list
        args += [a for v in _read_user_config(key) for a in ("-c", f"{key}={v}")]

    for key, fallback in _INHERITED_SCALAR_KEYS.items():
        values = _read_user_config(key)
        # Last value wins for a scalar key; take the user's own if they set one,
        # otherwise name the default explicitly so a repo-local value cannot win.
        args += ["-c", f"{key}={values[-1] if values else fallback}"]

    _trusted_cache = args
    return args


def _read_user_config(key: str) -> list[str]:
    """Values for `key` from the user's global and system git config only."""
    values: list[str] = []
    for scope in ("--global", "--system"):
        try:
            r = subprocess.run(
                ["git", "config", scope, "--get-all", key],
                capture_output=True,
                text=True,
                timeout=10,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            values += [v for v in r.stdout.splitlines() if v.strip()]
    return values


def _run_git(path: str, args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    cmd = ["git", *_trusted_overrides(), "-C", path, *args]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=_build_env(),
        )
    except subprocess.TimeoutExpired:
        # A hung git call (e.g. network/auth stall on pull) must not abort the
        # whole run. Return a synthetic failure so _sync_repo marks this repo
        # failed and continues. 124 mirrors GNU timeout's convention.
        return subprocess.CompletedProcess(
            cmd, returncode=124, stdout="", stderr=f"timed out after {timeout}s"
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
    """Sync one repo. Returns (status, reason) where status is pulled|up-to-date|skipped|failed.

    Only `pull` realistically times out (it's the sole network call); a 124 timeout on
    the local pre-pull checks below would fall through to their "skipped" branches, which
    is fine — the run still completes either way thanks to _run_git's TimeoutExpired catch.
    """
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


_MAX_NAMES_PER_REASON = 3
_MAX_REASON_LEN = 80


def _format_failures(failures: list[tuple[str, str]]) -> str:
    """Summarise failed repos grouped by reason.

    Per-repo reasons previously reached only `output.task_debug()`, which is
    suppressed without `--debug` -- so the log recorded "13 failed: <names>"
    with no cause, which is not actionable after the fact. Grouping matters
    because the common case is one shared cause (a network drop failing every
    repo at once), which would otherwise repeat the same message 13 times.
    """
    grouped: dict[str, list[str]] = {}
    for name, reason in failures:
        text = reason.strip() or "unknown error"
        if len(text) > _MAX_REASON_LEN:
            text = text[: _MAX_REASON_LEN - 1].rstrip() + "…"
        grouped.setdefault(text, []).append(name)

    parts = []
    for reason, names in grouped.items():
        shown = ", ".join(names[:_MAX_NAMES_PER_REASON])
        if len(names) > _MAX_NAMES_PER_REASON:
            shown = f"{shown}, +{len(names) - _MAX_NAMES_PER_REASON} more"
        parts.append(f"{shown} ({reason})")
    return "; ".join(parts)


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
    failures: list[tuple[str, str]] = []
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
            failures.append((os.path.basename(path.rstrip("/")), reason))

    if failures:
        return TaskResult(
            "git_sync", "failed", reason=f"{len(failures)} failed: {_format_failures(failures)}"
        )

    parts = []
    if n_pulled:
        parts.append(f"{n_pulled} pulled")
    if n_skipped:
        parts.append(f"{n_skipped} skipped")
    reason = ", ".join(parts) if parts else "no repos processed"
    return TaskResult("git_sync", "ok", reason=reason)
