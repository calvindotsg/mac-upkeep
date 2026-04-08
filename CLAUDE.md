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
| `uv run maintenance init` | Generate starter config (auto-detect tools) |
| `uv run maintenance show-config --default` | Show all available task options |
| `uv run maintenance notify-test` | Verify macOS notification permissions |

## Architecture

```
defaults.toml → bundled task definitions (11 tasks), loaded via importlib.resources
config.py     → TaskDef dataclass, load_task_defs(), resolve_variables(), get_brew_prefix(),
                Config.load() (3-layer merge: defaults.toml → user config → env vars)
tasks.py      → _build_cmd(), run_task(), _run(), run_all_tasks() data-driven loop,
                frequency scheduling, ANSI stripping
cli.py        → Typer app: run, tasks, init, show-config, setup, status, logs, notify-test
output.py     → TaskResult dataclass, Rich Live table TUI (interactive), Python logging (non-interactive)
notify.py     → macOS notifications via terminal-notifier (preferred) / osascript (fallback)
```

Entry point: `maintenance.cli:app` (registered in pyproject.toml `[project.scripts]`).

Task execution order defined in `defaults.toml` `[run] order`. Users override in `~/.config/maintenance/config.toml`.

## Key Patterns

### TOML-driven task registry

- **`defaults.toml` is the single source of truth.** Adding a built-in task = 1 TOML entry. No Python code changes needed. The file is bundled via `importlib.resources.files("maintenance")` and loaded at startup.
- **`TaskDef` fields are minimal.** `defaults.toml` only specifies fields that differ from `TaskDef` dataclass defaults (`frequency="weekly"`, `enabled=True`, `sudo=False`, `shell=""`, `require_file=""`, `timeout=300`). Weekly tasks omit `frequency`.
- **Config.load() reads user TOML once.** Parsed data is passed to `load_task_defs()` as a dict — avoids double file read. Brewfile and notification settings are extracted from the same parse.

### Detection and execution

- **`detect` is separate from `command`**: `detect` field specifies which binary to check with `shutil.which()`. This is separate from the command that runs. Reason: for sudo tasks, `_build_cmd()` prepends `["sudo", "-n"]` to the command — if detect used `cmd[0]`, it would check `shutil.which("sudo")` instead of the actual tool. Validated against topgrade's `require("binary")` pattern.
- **`_build_cmd()` composable**: `sudo` and `shell` are independent flags. Both branches (shell vs non-shell) merge before the sudo check. No early return in the shell branch — sudo wraps the entire invocation.
- **stdin closed for task subprocesses**: `stdin=subprocess.DEVNULL` prevents interactive tool hangs. Mole detects interactive mode via `[[ -t 0 ]]` (stdin is TTY) or unguarded `read_key` calls. Fisher uses `$last_pid` for job control which fails in non-interactive shells ([fisher#608](https://github.com/jorgebucaran/fisher/issues/608)), hence `shell = "fish --interactive -c"`.

### Filter-then-frequency contract

`_run()` has two early-return checks in strict order:
1. **Filter** (`force_tasks is not None and task_key not in force_tasks`) — unconditional
2. **Frequency** (`not dry_run and force_tasks is None and not _should_run()`) — conditional

Do not add conditions to the filter or remove the `force_tasks is None` guard from frequency. `require_file` tasks are checked in `run_all_tasks()` BEFORE calling `_run()`, respecting filter→enabled→file order.

### Frequency scheduling

- Thresholds are 6 days for weekly and 27 days for monthly (not 7/30 — buffer for launchd schedule drift after sleep/reboot). State tracked in `~/.local/state/maintenance/last-run.json`.
- **Safety net**: prevents redundant runs from launchd coalescing, manual `maintenance run`, or formula regressions re-enabling RunAtLoad. Do not remove even with `run_at_load false`. Homebrew defaults `run_at_load` to `true` ([service.rb:55](https://github.com/Homebrew/brew/blob/main/Library/Homebrew/service.rb)) — undocumented in Formula Cookbook.
- Timestamps only update on successful non-dry-run execution. Corrupt/missing state file silently triggers re-run.

### Output and notifications

- **Interactive detection**: `sys.stdout.isatty()` switches between Rich Live table and Python logging. Same code path, different presentation.
- **Rich Live TUI state separation**: `_TaskState` holds status/reason/duration; `_generate_table()` renders. Debug output scrolls ABOVE the pinned table via `self._live.console.print()` — never put debug content into `_TaskState`.
- **Notifications always fire**: `notify()` is called regardless of `output.interactive`. The headless + notification + click-to-act pattern means notifications are the user's feedback channel for scheduled runs.
- **terminal-notifier preferred**: `shutil.which("terminal-notifier")` tries the richer tool first. Fallback to osascript loses `-group` (dedup), `-activate` (focus terminal), `-open` (click action).
- **Bundle ID detection chain**: `CMUX_BUNDLE_ID` env var → Ghostty.app plist via `defaults read` → `com.apple.Terminal` fallback.
- **Rich is a transitive dependency**: `typer>=0.12` requires `rich>=12.3.0`. Using Rich adds zero new runtime dependencies.

### sudo + HOME

`sudo -n` with full path `$BREW_PREFIX/bin/mo`. Sudoers `env_keep += "HOME"` preserves user's home directory (otherwise `HOME=/var/root` and mole misses user caches). The `sudo` field in `TaskDef` exists instead of embedding `sudo` in the command so that: (a) dry-run can skip sudo, (b) `detect` infers the correct binary, (c) `maintenance setup` can generate sudoers rules.

### Brew prefix detection

`get_brew_prefix()` lives in `config.py` (moved from tasks.py). Called once during `Config.load()` for `${BREW_PREFIX}` variable resolution. Subprocess call with architecture fallback — portable across Apple Silicon (`/opt/homebrew`) and Intel (`/usr/local`).

## Non-Obvious Constraints

- `gcloud-cli` is a Homebrew cask, not a formula — can't be a formula dependency. Auto-detected at runtime.
- `mo_purge` non-interactive mode (stdin closed) auto-selects items not modified in the last 7 days. Interactive mode shows a TUI selector.
- `mo_optimize` has three unguarded `read_key` calls that block on stdin. With stdin closed, `read` returns non-zero → prompts skipped. Safe operations still run.
- `uv cache prune` requires `--force` in environments with long-running `uvx` processes (MCP servers). Without it, prune blocks on the cache lock ([astral-sh/uv#16112](https://github.com/astral-sh/uv/issues/16112)).
- NOPASSWD in sudoers bypasses PAM entirely — no interaction with Touch ID (pam_tid) setup.
- **launchd PATH requires `std_service_path_env`**: launchd default PATH is `/usr/bin:/bin:/usr/sbin:/sbin`. Without `environment_variables PATH: std_service_path_env` in the formula service block, all Homebrew-installed tools fail `shutil.which()`. Notifications fall back to osascript.
- **No Python FileHandler for log file**: launchd redirects stderr to the log file. Python `FileHandler` causes duplicate lines. Log rotation handled by macOS newsyslog.d.
- **newsyslog.d config** printed by `maintenance setup` but NOT auto-installed (requires `sudo tee`).
- **`terminal-notifier` is optional** — installed via `brew install terminal-notifier`.
- **Do not open terminal windows from launchd** — fragile (focus stealing, macOS 13+ permission escalation). Use headless + notification + click-to-act.
- **Testing**: `Config.load()` calls `get_brew_prefix()` which runs `subprocess.run(["brew", "--prefix"])`. Tests that mock `subprocess.run` or `shutil.which` will capture this call too (shared module objects). Mock `maintenance.config.get_brew_prefix` directly in `init` command tests.

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

**Adapt:**
- TOML-driven task definitions with `importlib.resources` bundling — for any CLI needing an extensible command registry where adding a task shouldn't require code changes
- `init` with system detection via `shutil.which()` — for any CLI replacing static example config files with generated, system-aware configs
- 3-layer config merge (bundled `defaults.toml` → user `config.toml` → env vars) with field-level override — for any CLI needing layered configuration
- `${VAR}` variable resolution in TOML fields — for portable paths across architectures (`${BREW_PREFIX}` resolves differently on Apple Silicon vs Intel)
- Rich Live table TUI (`isatty()` detection + `_TaskState` + `Live` + `console.print()` above pinned table) for any CLI running interactively AND via scheduler
- terminal-notifier with osascript fallback for any macOS launchd service needing actionable notifications
- `repository_dispatch` + GitHub App for cross-repo automation
- `subprocess.run(stdin=subprocess.DEVNULL)` for any CLI orchestrator wrapping interactive tools
- Per-task frequency scheduling with XDG state file + threshold buffers for any periodic CLI tool
- newsyslog.d config generation via setup command for any macOS launchd service needing log rotation

**Project-specific (do not copy):**
- Mole CLI wrapper and sudo/HOME/sudoers configuration
- Homebrew tap formula + poet resource regeneration
