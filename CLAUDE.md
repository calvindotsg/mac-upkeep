# CLAUDE.md

> For dev setup and commit conventions, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Quick Commands

| Command | Purpose |
|---------|---------|
| `uv sync` | Install dependencies |
| `uv run ruff check src/ tests/` | Lint |
| `uv run ruff format src/ tests/` | Format |
| `uv run pytest` | Run tests |
| `uv run pytest --cov` | Run tests with coverage |
| `uv run mac-upkeep run --dry-run` | Test CLI without side effects |
| `uv run mac-upkeep init` | Generate starter config (auto-detect tools) |
| `uv run mac-upkeep show-config --default` | Show all available task options |
| `uv run mac-upkeep notify-test` | Verify macOS notification permissions |

## Architecture

```
defaults.toml → bundled task definitions, loaded via importlib.resources
config.py     → TaskDef dataclass, load_task_defs(), resolve_variables(), get_brew_prefix(),
                Config.load() (3-layer merge: defaults.toml → user config → env vars)
tasks.py      → _build_cmd(), run_task(), _run(), run_all_tasks() data-driven loop,
                frequency scheduling, format_last_run(), format_next_run(), ANSI stripping
cli.py        → Typer app: run, tasks, init, show-config, setup, status (dashboard), logs, notify-test
output.py     → TaskResult dataclass, Rich Live table TUI (interactive), Python logging (non-interactive)
notify.py     → macOS notifications via terminal-notifier (preferred) / osascript (fallback)
```

Entry point: `mac_upkeep.cli:app` (registered in pyproject.toml `[project.scripts]`).

Task execution order defined in `defaults.toml` `[run] order`. Users override in `~/.config/mac-upkeep/config.toml`.

### Adding a task

Add a `[tasks.<name>]` entry to `defaults.toml`. No Python code changes needed. Fields that match `TaskDef` defaults can be omitted (weekly frequency, enabled, no sudo, no shell, 300s timeout). Run `uv run pytest` to validate. If the task requires a binary not in PATH, set `detect` to the binary name.

## Key Patterns

### TOML-driven task registry

- **`defaults.toml` is the single source of truth.** Adding a built-in task = 1 TOML entry. No Python code changes needed. The file is bundled via `importlib.resources.files("mac_upkeep")` and loaded at startup.
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

- Thresholds are 20 hours for daily, 6 days for weekly, 27 days for monthly (not 24h/7d/30d — buffer for launchd schedule drift after sleep/reboot). State tracked in `~/.local/state/mac-upkeep/last-run.json`.
- **Safety net**: prevents redundant runs from RunAtLoad boot triggers, launchd coalescing, and manual `mac-upkeep run`. `run_at_load true` is intentional — `StartCalendarInterval` does NOT coalesce from power-off (only sleep), so RunAtLoad is essential for laptops that reboot frequently.
- Timestamps only update on successful non-dry-run execution. Corrupt/missing state file silently triggers re-run.
- **`FREQUENCY_THRESHOLDS` is dual-purpose**: used for gating in `_should_run()` and for display in `format_next_run()`. `format_next_run()` accepts an optional `state` dict parameter to avoid redundant `_load_state()` calls — the `tasks` command pre-loads state once; `_run()` skip path omits it (one-off read is fine).
- **Status column priority in `tasks` command**: `disabled → not found → ready` mirrors the check order in `run_task()` (disabled check then detection check) but is computed independently in `cli.py` using `td.enabled` and `shutil.which(td.detect)`. `td.detect` is already variable-resolved and auto-inferred by `Config.load()`, so `shutil.which(td.detect)` works directly — no raw TOML variable resolution needed.

### Output and notifications

- **Interactive detection**: `sys.stdout.isatty()` switches between Rich Live table and Python logging. Same code path, different presentation.
- **Rich Live TUI state separation**: `_TaskState` holds status/reason/duration; `_generate_table()` renders. Debug output scrolls ABOVE the pinned table via `self._live.console.print()` — never put debug content into `_TaskState`.
- **Notifications fire on activity**: `notify()` is called when at least one task ran or failed, regardless of `output.interactive`. Suppressed when all tasks skip (e.g., RunAtLoad boot with recent timestamps). The headless + notification + click-to-act pattern means notifications are the user's feedback channel for scheduled runs.
- **terminal-notifier preferred**: `shutil.which("terminal-notifier")` tries the richer tool first. Fallback to osascript loses `-group` (dedup), `-activate` (focus terminal), `-open` (click action).
- **Bundle ID detection chain**: `CMUX_BUNDLE_ID` env var → Ghostty.app plist via `defaults read` → `com.apple.Terminal` fallback.
- **Rich is a transitive dependency**: `typer>=0.12` requires `rich>=12.3.0`. Using Rich adds zero new runtime dependencies.
- **`status` dashboard graceful degradation**: if `_get_service_info()` returns None (brew not installed, service not registered, or JSON parse failure), the service header is skipped and only the task scheduling summary is shown. Reuses `format_last_run()` and `format_next_run()` from tasks.py. Test by patching `mac_upkeep.cli._get_service_info` directly rather than mocking subprocess.run (avoids interfering with Config.load() → get_brew_prefix() subprocess call).

### Handler-dispatched tasks

A TaskDef with `handler="<name>"` and empty `command` bypasses subprocess building: `run_all_tasks` routes it through `_run_handler`, which applies filter + frequency + `detect` gates then calls `HANDLERS[name](config, output, dry_run)`. Handler modules register themselves in `tasks._register_handlers()` (called at import). `KNOWN_HANDLERS` drives config validation — unknown handler names are rejected early. Handlers emit per-step output via `output.task_debug()` and return one aggregate `TaskResult`.

### sudo + HOME

`sudo -n` with full path `$BREW_PREFIX/bin/mo`. Sudoers `env_keep += "HOME"` preserves user's home directory (otherwise `HOME=/var/root` and mole misses user caches). The `sudo` field in `TaskDef` exists instead of embedding `sudo` in the command so that: (a) dry-run can skip sudo, (b) `detect` infers the correct binary, (c) `mac-upkeep setup` can generate sudoers rules.

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
- **newsyslog.d config** printed by `mac-upkeep setup` but NOT auto-installed (requires `sudo tee`).
- **`terminal-notifier` is optional** — installed via `brew install terminal-notifier`.
- **Do not open terminal windows from launchd** — fragile (focus stealing, macOS 13+ permission escalation). Use headless + notification + click-to-act.
- **Testing**: `Config.load()` calls `get_brew_prefix()` which runs `subprocess.run(["brew", "--prefix"])`. Tests that mock `subprocess.run` or `shutil.which` will capture this call too (shared module objects). Mock `mac_upkeep.config.get_brew_prefix` directly in `init` command tests.
- **`git_sync` SSH auth under launchd** relies on `~/.ssh/config` `IdentityAgent` (path-based, e.g. 1Password socket). `SSH_AUTH_SOCK` env vars are NOT inherited by LaunchAgents, so env-based agent forwarding won't work here — the `IdentityAgent` directive is the supported path.

## Release Process

Automated via release-please + homebrew-tap dispatch:

1. Commit changes using conventional commits, push to main
2. release-please creates a release PR (bumps version in `pyproject.toml`, updates CHANGELOG.md)
3. Release workflow auto-updates `uv.lock` on the PR branch, test.yml validates via `uv lock --check`
4. Merge the release PR → GitHub release + tag created → `bump-tap` dispatches to homebrew-tap, `pypi-publish` publishes to PyPI
5. Verify: check homebrew-tap Actions tab for successful formula update

## Reusable Patterns

This repo serves as a reference for Python CLI projects using Typer + UV. See [docs/reusable-patterns.md](docs/reusable-patterns.md) for copy-ready workflows, configs, and adaptable patterns.
