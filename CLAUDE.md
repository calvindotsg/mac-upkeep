# CLAUDE.md

## Quick Commands

| Command | Purpose |
|---------|---------|
| `uv sync` | Install dependencies |
| `uv run ruff check src/ tests/` | Lint |
| `uv run ruff format src/ tests/` | Format |
| `uv run pytest` | Run tests |
| `uv run pytest --cov` | Run tests with coverage |
| `uv run maintenance run --dry-run` | Test CLI without side effects |
| `uv run maintenance notify-test` | Verify macOS notification permissions |

## Architecture

```
cli.py    → Typer app, subcommand dispatch, signal handling, logging setup
tasks.py  → run_task() helper, run_all_tasks() orchestrator, ANSI stripping
config.py → TOML loading (tomllib), env var overrides, Brewfile discovery
output.py → TaskResult dataclass, interactive/non-interactive output (Rich)
notify.py → macOS notifications via osascript, summary formatting
```

Entry point: `maintenance.cli:app` (registered in pyproject.toml `[project.scripts]`).

## Key Patterns

- **Auto-detection**: `shutil.which(cmd)` checks if a tool is installed before running. Missing tools are skipped, not errors.
- **Graceful failure**: Each task wrapped in try/except. Individual failures log a warning and continue to the next task. The CLI always exits 0 unless interrupted (130).
- **ANSI stripping**: Mole emits color codes even without TTY. `re.sub(r'\x1b\[[0-9;]*m', '', output)` strips them from captured output.
- **sudo + HOME**: `sudo -n` with full path `$BREW_PREFIX/bin/mo`. Sudoers `env_keep += "HOME"` preserves user's home directory (otherwise `HOME=/var/root` and mole misses user caches).
- **stdin closed for subprocesses**: `subprocess.run` passes `stdin=subprocess.DEVNULL` to prevent interactive tool hangs. Mole detects interactive mode via `[[ -t 0 ]]` (stdin is TTY) or unguarded `read_key` calls — `capture_output=True` only pipes stdout/stderr, leaving stdin inherited from the parent terminal.
- **fisher needs `--interactive`**: Fisher uses `$last_pid` for job control which fails in non-interactive shells ([fisher#608](https://github.com/jorgebucaran/fisher/issues/608)).
- **Task ordering**: `brew_bundle` runs last because `mo_clean` internally runs `brew autoremove`. Running bundle cleanup after avoids the [cascading removal bug](https://github.com/homebrew/brew/issues/21350).
- **Brew prefix detection**: `subprocess.run(["brew", "--prefix"])` with architecture fallback. Portable across Apple Silicon and Intel.
- **Missed schedules**: launchd catches up after sleep (coalesced). Powered off → skipped until next Monday.
- **Interactive detection**: `sys.stdout.isatty()` switches between Rich console (interactive) and Python logging (non-interactive/launchd). Same code path, different presentation. Zero visual change to launchd logs.
- **Rich is a transitive dependency**: `typer>=0.12` requires `rich>=12.3.0`. Homebrew formula already bundles it. Using Rich adds zero new runtime dependencies.
- **osascript notifications**: `/usr/bin/osascript -e 'display notification ...'` works from launchd user agents. 5-second timeout, never fatal. Notification permissions may need granting in System Settings > Notifications.
- **TaskResult over bool**: `run_task` returns a structured `TaskResult(name, status, reason, duration)` instead of `bool`. Enables distinct skip/fail reporting, per-task timing, and notification content — without coupling task execution to output formatting.

## Non-Obvious Constraints

- `gcloud-cli` is a Homebrew cask, not a formula — can't be a formula dependency. Auto-detected at runtime.
- `mo_purge` non-interactive mode (stdin closed) auto-selects items not modified in the last 7 days. Recently modified project artifacts are preserved. Interactive mode (direct `mo purge`) shows a TUI selector with manual choice.
- `mo_optimize` has three unguarded `read_key` calls (security fixes, updates, auto-fix) that block on stdin if their conditions are met. With stdin closed, `read` returns non-zero → `read_key` yields QUIT → prompts skipped. Safe operations (checks, health scans) still run.
- `uv cache prune` requires `--force` in environments with long-running `uvx` processes (MCP servers, dev tools). Without it, prune blocks on the cache lock held by `uvx` processes ([astral-sh/uv#16112](https://github.com/astral-sh/uv/issues/16112)).
- NOPASSWD in sudoers bypasses PAM entirely — no interaction with Touch ID (pam_tid) setup.
- `check_touchid` is already whitelisted in mole's optimize whitelist, preventing false positive security fix suggestions.
- `poet -r <package>` calls PyPI API for the main package. Fails if not published to PyPI. Tap workflow gracefully falls back to updating only URL/sha256 (resource blocks unchanged). `brew update-python-resources` is the Homebrew-native alternative but requires macOS + Homebrew on the CI runner.
- **Node.js formulas don't need resource blocks** — npm resolves the full dependency tree at install time via `std_npm_args`. Only URL + sha256 need updating on release.
- **`repository_dispatch` is fire-and-forget** — the source repo won't know if the tap update succeeded. Check the homebrew-tap Actions tab after a release to verify.
- **`.release-please-manifest.json` must exist** alongside `release-please-config.json`. Tracks current version (`{".": "1.0.0"}`). Without it, release-please-action@v4 fails with "Missing required manifest versions." release-please updates this file automatically on release.
- **GitHub App token `repositories:` scoping** — the `bump-tap` job scopes its token to `repositories: homebrew-tap` so the dispatch has write access to the target repo. Without this parameter, the token defaults to the current repo only.

## Release Process

Automated via release-please + homebrew-tap dispatch:

1. Commit changes using conventional commits, push to main
2. release-please creates a release PR (bumps version in `pyproject.toml`, updates CHANGELOG.md)
3. Merge the release PR → GitHub release + tag created → `bump-tap` job dispatches to homebrew-tap automatically
4. Verify: check homebrew-tap Actions tab for successful formula update

## Reusable Patterns

This repo serves as a reference for Python CLI projects using Typer + UV.

**Copy directly** (adjust versions/paths):
- `.github/workflows/test.yml` — lint + test CI on macOS
- `.github/workflows/release.yml` — release-please with GitHub App token + tap dispatch
- `release-please-config.json` + `.release-please-manifest.json` — config and version tracking (both required)
- `pyproject.toml` structure — Hatchling build, Ruff lint+format, pytest config
- `CONTRIBUTING.md` — dev setup, commit conventions, PR process

**Follow structure:**
- CLAUDE.md: quick commands → architecture → key patterns → constraints → release → reusable patterns
- README.md: install → tasks → usage → config → prerequisites
- Config pattern: TOML file + env var overrides + auto-discovery fallback chain

**Adapt:**
- Rich dual-mode output (`isatty()` detection) for any CLI running interactively AND via scheduler
- osascript notifications for any macOS launchd service needing user feedback
- `repository_dispatch` + GitHub App for cross-repo automation across multiple source repos
- `subprocess.run(stdin=subprocess.DEVNULL)` for any CLI orchestrator wrapping interactive tools — prevents invisible prompt hangs when stdout is piped but stdin leaks from parent terminal

**Project-specific (do not copy):**
- Mole CLI wrapper and sudo/HOME/sudoers configuration
- Task auto-detection via `shutil.which()` (specific to multi-tool orchestrator)
- Homebrew tap formula + poet resource regeneration
