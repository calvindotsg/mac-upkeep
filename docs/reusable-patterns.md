# Reusable Patterns

This repo serves as a reference for Python CLI projects using Typer + UV.

## Copy directly

Adjust versions/paths:

- `.github/workflows/test.yml` — lint + test CI on macOS
- `.github/workflows/release.yml` — release-please with GitHub App token + tap dispatch + PyPI Trusted Publishing (OIDC)
- `release-please-config.json` + `.release-please-manifest.json` — config and version tracking (both required)
- `pyproject.toml` structure — Hatchling build, Ruff lint+format, pytest config
- `CONTRIBUTING.md` — dev setup, commit conventions, PR process

## Adapt

- TOML-driven task definitions with `importlib.resources` bundling — for any CLI needing an extensible command registry where adding a task shouldn't require code changes
- Handler registry dispatch (`HANDLERS: dict[str, Callable]` + `_run_handler`) — for any TOML-driven CLI that needs some tasks to run as Python rather than subprocess commands, without coupling the framework to specific handler implementations
- `init` with system detection via `shutil.which()` — for any CLI replacing static example config files with generated, system-aware configs
- 3-layer config merge (bundled `defaults.toml` → user `config.toml` → env vars) with field-level override — for any CLI needing layered configuration
- `${VAR}` variable resolution in TOML fields — for portable paths across architectures (`${BREW_PREFIX}` resolves differently on Apple Silicon vs Intel)
- Rich Live table TUI (`isatty()` detection + `_TaskState` + `Live` + `console.print()` above pinned table) for any CLI running interactively AND via scheduler
- terminal-notifier with osascript fallback for any macOS launchd service needing actionable notifications
- `repository_dispatch` + GitHub App for cross-repo automation
- `subprocess.run(stdin=subprocess.DEVNULL)` for any CLI orchestrator wrapping interactive tools
- Per-task frequency scheduling with XDG state file + threshold buffers + humanized next-run/last-run formatting for any periodic CLI tool
- `RunAtLoad true` + application-level frequency thresholds for reliable launchd scheduling on laptops — `StartCalendarInterval` does NOT coalesce from power-off (only sleep), so RunAtLoad is the reliable trigger with thresholds preventing over-running
- Notification suppression when all tasks skip (`has_activity` guard) — for any RunAtLoad service that would otherwise notify on every boot
- newsyslog.d config generation via setup command for any macOS launchd service needing log rotation

## Project-specific (do not copy)

- Mole CLI wrapper and sudo/HOME/sudoers configuration
- Homebrew tap formula + poet resource regeneration
