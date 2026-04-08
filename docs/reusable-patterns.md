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
- `init` with system detection via `shutil.which()` — for any CLI replacing static example config files with generated, system-aware configs
- 3-layer config merge (bundled `defaults.toml` → user `config.toml` → env vars) with field-level override — for any CLI needing layered configuration
- `${VAR}` variable resolution in TOML fields — for portable paths across architectures (`${BREW_PREFIX}` resolves differently on Apple Silicon vs Intel)
- Rich Live table TUI (`isatty()` detection + `_TaskState` + `Live` + `console.print()` above pinned table) for any CLI running interactively AND via scheduler
- terminal-notifier with osascript fallback for any macOS launchd service needing actionable notifications
- `repository_dispatch` + GitHub App for cross-repo automation
- `subprocess.run(stdin=subprocess.DEVNULL)` for any CLI orchestrator wrapping interactive tools
- Per-task frequency scheduling with XDG state file + threshold buffers for any periodic CLI tool
- newsyslog.d config generation via setup command for any macOS launchd service needing log rotation

## Project-specific (do not copy)

- Mole CLI wrapper and sudo/HOME/sudoers configuration
- Homebrew tap formula + poet resource regeneration
