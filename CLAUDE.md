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
tasks.py  → run_task() helper, run_all_tasks() orchestrator, frequency scheduling, ANSI stripping
config.py → TOML loading (tomllib), env var overrides, frequency defaults, Brewfile discovery
output.py → TaskResult dataclass, Rich Live table TUI (interactive), Python logging (non-interactive)
notify.py → macOS notifications via terminal-notifier (preferred) / osascript (fallback), bundle ID detection
```

Entry point: `maintenance.cli:app` (registered in pyproject.toml `[project.scripts]`).

11 tasks in order: `brew_update` → `brew_upgrade` → `gcloud` → `pnpm` → `uv` → `fisher` → `mo_clean` → `mo_optimize` → `mo_purge` → `brew_cleanup` → `brew_bundle`.

## Key Patterns

- **Auto-detection**: `shutil.which(cmd)` checks if a tool is installed before running. Missing tools are skipped, not errors.
- **Graceful failure**: Each task wrapped in try/except. Individual failures log a warning and continue to the next task. The CLI always exits 0 unless interrupted (130).
- **ANSI stripping**: Mole emits color codes even without TTY. `re.sub(r'\x1b\[[0-9;]*m', '', output)` strips them from captured output.
- **sudo + HOME**: `sudo -n` with full path `$BREW_PREFIX/bin/mo`. Sudoers `env_keep += "HOME"` preserves user's home directory (otherwise `HOME=/var/root` and mole misses user caches).
- **stdin closed for task subprocesses**: Task execution passes `stdin=subprocess.DEVNULL` to prevent interactive tool hangs. Mole detects interactive mode via `[[ -t 0 ]]` (stdin is TTY) or unguarded `read_key` calls. Utility calls (osascript `-e`, `defaults read`) don't need it — they never read stdin.
- **fisher needs `--interactive`**: Fisher uses `$last_pid` for job control which fails in non-interactive shells ([fisher#608](https://github.com/jorgebucaran/fisher/issues/608)).
- **Task ordering**: `brew_update` → `brew_upgrade` run first (fresh package DB before any operations). `brew_cleanup` runs after `mo_clean` (which runs `brew autoremove`). `brew_bundle` runs last to avoid the [cascading removal bug](https://github.com/homebrew/brew/issues/21350).
- **Brew prefix detection**: `subprocess.run(["brew", "--prefix"])` with architecture fallback. Portable across Apple Silicon and Intel.
- **Missed schedules**: Formula sets `run_at_load false` + `environment_variables PATH: std_service_path_env`. launchd only fires at the scheduled time with correct PATH. Powered off → skipped until next scheduled run. Frequency scheduling prevents duplicate runs from launchd coalescing after sleep.
- **Interactive detection**: `sys.stdout.isatty()` switches between Rich Live table (interactive) and Python logging (non-interactive/launchd). Same code path, different presentation. Zero visual change to launchd logs.
- **Rich Live TUI state separation**: `_TaskState` objects hold status/reason/duration; `_generate_table()` renders them. Debug output scrolls ABOVE the pinned table via `self._live.console.print()` — never put debug content into `_TaskState` or it overwrites the table. Task-name separator headers (`── taskname ──`) group output between tasks.
- **Rich is a transitive dependency**: `typer>=0.12` requires `rich>=12.3.0`. Homebrew formula already bundles it. Using Rich adds zero new runtime dependencies.
- **Notifications always fire**: `notify()` is called regardless of `output.interactive`. Do not add back `not output.interactive` guard — the headless + notification + click-to-act pattern means notifications are the user's feedback channel for scheduled runs, and a summary for interactive runs.
- **terminal-notifier preferred, osascript fallback**: `shutil.which("terminal-notifier")` tries the richer tool first. Fallback to osascript silently loses `-group` (dedup), `-activate` (focus terminal), and `-open` (click action). `terminal-notifier` is optional — install via `brew install terminal-notifier`.
- **Bundle ID detection chain**: `CMUX_BUNDLE_ID` env var → Ghostty.app plist via `defaults read` → `com.apple.Terminal` fallback. Used by `terminal-notifier -activate` to focus the correct terminal on notification click.
- **TaskResult over bool**: `run_task` returns a structured `TaskResult(name, status, reason, duration)` instead of `bool`. Enables distinct skip/fail reporting, per-task timing, and notification content — without coupling task execution to output formatting.
- **Per-task frequency scheduling**: Tasks run on weekly or monthly schedules. State tracked in `~/.local/state/maintenance/last-run.json` (XDG_STATE_HOME). Thresholds are 6 days for weekly and 27 days for monthly (not 7/30 — buffer for launchd schedule drift after sleep/reboot). Timestamps only update on successful non-dry-run execution. Corrupt/missing state file silently triggers re-run.
- **`force_tasks` semantics**: `force_tasks=None` (no `--force`) means frequency scheduling applies — tasks skip if ran recently. `force_tasks={"task"}` runs only that task, frequency bypassed. `--force all` sets force_tasks to all task names (all run, frequency bypassed). The `is not None` check is the filter gate; `force_tasks is None` in the frequency check ensures forced tasks bypass scheduling.
- **`_run()` filter-then-frequency contract**: `_run()` has two early-return checks in strict order: (1) filter (`force_tasks is not None and task_key not in force_tasks`) — unconditional; (2) frequency (`not dry_run and force_tasks is None and not _should_run()`) — conditional. Do not add conditions to the filter or remove the `force_tasks is None` guard from frequency.
- **brew_bundle validates then delegates**: The brew_bundle block handles filter + validation (disabled, no Brewfile) then delegates to `_run()` for frequency + dry_run + execution. Do not add frequency or dry_run logic to the brew_bundle block.
- **TASKS dict is the task registry**: `TASKS` dict in tasks.py is the single source of truth for task names and descriptions. `ALL_TASK_NAMES`, shell completion, and `maintenance tasks` derive from it. Adding a task requires 4 updates: `TASKS` (tasks.py), `DEFAULT_TASKS` (config.py), `DEFAULT_FREQUENCIES` (config.py), `run_all_tasks()` (tasks.py). `DEFAULT_TASKS`/`DEFAULT_FREQUENCIES` can't be derived from `TASKS` due to circular import (tasks.py → config.py).
- **launchd PATH requires `std_service_path_env`**: launchd default PATH is `/usr/bin:/bin:/usr/sbin:/sbin`. Without `environment_variables PATH: std_service_path_env` in the formula service block, all Homebrew-installed tools fail `shutil.which()` auto-detection. Notifications fall back to osascript → click opens Script Editor. Only mole tasks work (full path via `get_brew_prefix()`).
- **Frequency scheduling as safety net**: Prevents redundant runs from launchd coalescing, manual `maintenance run`, or formula regressions re-enabling RunAtLoad. Do not remove even with `run_at_load false`. Homebrew defaults `run_at_load` to `true` ([service.rb:55](https://github.com/Homebrew/brew/blob/main/Library/Homebrew/service.rb)) — undocumented in Formula Cookbook.
- **No Python FileHandler for log file**: launchd redirects stderr to the log file. Adding a Python `FileHandler` to the same path causes duplicate lines. Python's rotating handlers (`TimedRotatingFileHandler`) don't work for periodic CLI tools (process exits between runs, rotation never triggers). Log rotation is handled by macOS newsyslog.d instead.
- **uv.lock sync on release**: release-please bumps `pyproject.toml` version but can't run `uv lock`. The release workflow auto-updates `uv.lock` on the release-please PR branch (checkout → `uv lock` → commit). `uv lock --check` in test.yml catches stale lockfiles from any source. The check runs **before** `uv sync` — sync silently fixes staleness, masking the problem.

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
- **`.release-please-manifest.json` must exist** alongside `release-please-config.json`. Tracks current version (e.g. `{".": "1.2.0"}`). Without it, release-please-action@v4 fails with "Missing required manifest versions." release-please updates this file automatically on release.
- **GitHub App token `repositories:` scoping** — the `bump-tap` job scopes its token to `repositories: homebrew-tap` so the dispatch has write access to the target repo. Without this parameter, the token defaults to the current repo only.
- **Do not open terminal windows from launchd** — research confirmed this is fragile (focus stealing, macOS 13+ permission escalation, missing env vars, broken when screen locked). Use headless + notification + click-to-act instead.
- **newsyslog.d rotation config** is printed by `maintenance setup` but NOT auto-installed (requires `sudo tee /etc/newsyslog.d/maintenance.conf`). Format: monthly rotation on 1st at midnight (`$M1D0`), 12 backups, gzip (`GN`), no signal.
- **`terminal-notifier` is optional** — installed via `brew install terminal-notifier`. When absent, notifications work via osascript but lose click actions and deduplication.

## Release Process

Automated via release-please + homebrew-tap dispatch:

1. Commit changes using conventional commits, push to main
2. release-please creates a release PR (bumps version in `pyproject.toml`, updates CHANGELOG.md)
3. Release workflow auto-updates `uv.lock` on the PR branch, test.yml validates via `uv lock --check`
4. Merge the release PR → GitHub release + tag created → `bump-tap` job dispatches to homebrew-tap automatically
5. Verify: check homebrew-tap Actions tab for successful formula update

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
- README.md: install → tasks → usage → configuration → license
- Config pattern: TOML file + env var overrides + auto-discovery fallback chain

**Adapt:**
- Rich Live table TUI (`isatty()` detection + `_TaskState` + `Live` + `console.print()` above pinned table) for any CLI running interactively AND via scheduler
- terminal-notifier with osascript fallback for any macOS launchd service needing actionable notifications (click-to-focus, click-to-open-log)
- `repository_dispatch` + GitHub App for cross-repo automation across multiple source repos
- `subprocess.run(stdin=subprocess.DEVNULL)` for any CLI orchestrator wrapping interactive tools — prevents invisible prompt hangs when stdout is piped but stdin leaks from parent terminal
- Per-task frequency scheduling with XDG state file (`~/.local/state/<app>/last-run.json`) + threshold buffers for any periodic CLI tool
- newsyslog.d config generation via setup command for any macOS launchd service needing log rotation (macOS built-in, zero deps, no Python rotating handlers needed)

**Project-specific (do not copy):**
- Mole CLI wrapper and sudo/HOME/sudoers configuration
- Task auto-detection via `shutil.which()` (specific to multi-tool orchestrator)
- Homebrew tap formula + poet resource regeneration
- brew_bundle Brewfile validation and execution delegation
