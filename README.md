# mac-upkeep

[![PyPI](https://img.shields.io/pypi/v/mac-upkeep)](https://pypi.org/project/mac-upkeep/)
[![CI](https://img.shields.io/github/actions/workflow/status/calvindotsg/mac-upkeep/test.yml?branch=main)](https://github.com/calvindotsg/mac-upkeep/actions)
[![Python](https://img.shields.io/pypi/pyversions/mac-upkeep)](https://pypi.org/project/mac-upkeep/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey?logo=apple&logoColor=black)](https://github.com/calvindotsg/mac-upkeep)

Automated macOS maintenance CLI. Runs Homebrew updates, dev tool cache cleanup (gcloud, pnpm, uv), Fish plugin updates, system optimization, and Brewfile enforcement on boot + weekly via `brew services` — zero config required.

![mac-upkeep demo](demo.gif)

## Install

```bash
brew install calvindotsg/tap/mac-upkeep
brew services start mac-upkeep  # runs on boot + Monday 12 PM
```

Or via [uv](https://docs.astral.sh/uv/):

```bash
uv tool install mac-upkeep   # persistent install
uvx mac-upkeep run            # one-off without installing
```

## Tasks

| Task | Description | Schedule |
|------|-------------|----------|
| `brew_update` | Update Homebrew package database | Weekly |
| `brew_upgrade` | Upgrade outdated formulae and casks | Weekly |
| `gcloud` | Update Google Cloud SDK components | Monthly |
| `pnpm` | Prune pnpm content-addressable store | Monthly |
| `uv` | Prune uv package cache | Monthly |
| `fisher` | Update Fish shell plugins | Weekly |
| `mo_clean` | Clean system and user caches ([mole](https://github.com/nicehash/mole)) | Weekly |
| `mo_optimize` | Optimize DNS, Spotlight, fonts, Dock ([mole](https://github.com/nicehash/mole)) | Weekly |
| `mo_purge` | Remove old project artifacts ([mole](https://github.com/nicehash/mole)) | Monthly |
| `brew_cleanup` | Remove old versions and cache files | Monthly |
| `brew_bundle` | Remove packages not in Brewfile | Weekly |

Tasks auto-detect installed tools — missing tools are skipped. Use `--force <task>` to run a specific task on demand.

```bash
mac-upkeep tasks  # See all tasks with frequency and last-run status
```

## Usage

```bash
mac-upkeep run                       # Run tasks (frequency-checked)
mac-upkeep run --dry-run             # Preview without executing
mac-upkeep run --force brew_update   # Run only brew_update
mac-upkeep run --force all           # Run all, ignoring schedule
mac-upkeep run --debug               # Verbose output
mac-upkeep tasks                     # List tasks with status
mac-upkeep init                      # Generate config (detects your tools)
mac-upkeep show-config --default     # Show all available task options
mac-upkeep show-config               # Show your config overrides
mac-upkeep setup                     # Print sudoers rules
mac-upkeep status                    # Show brew service status
mac-upkeep logs                      # View last 20 log lines
mac-upkeep logs -f                   # Follow logs
mac-upkeep --version                 # Show version
```

## Configuration

Works out of the box with zero configuration. To customize, generate a starter config:

```bash
mac-upkeep init
```

This probes your system, detects installed tools, and writes a commented config to `~/.config/mac-upkeep/config.toml`. Only detected tasks are listed. Built-in defaults apply automatically — uncomment lines to override.

To see all available tasks and options:

```bash
mac-upkeep show-config --default
```

### Override examples

```toml
# ~/.config/mac-upkeep/config.toml

# Disable a task
[tasks.gcloud]
enabled = false

# Change frequency (weekly or monthly)
[tasks.brew_update]
frequency = "monthly"

# Set Brewfile path explicitly
[paths]
brewfile = "~/.config/Brewfile"
```

### Custom tasks

Add your own tasks using the same format:

```toml
[tasks.docker_prune]
description = "Prune Docker system"
command = "docker system prune -f"
detect = "docker"
frequency = "monthly"

# Control execution order
[run]
order = ["brew_update", "brew_upgrade", "docker_prune", "brew_cleanup", "brew_bundle"]
```

### Environment variables

```bash
MAC_UPKEEP_GCLOUD=false mac-upkeep run              # Disable a task
MAC_UPKEEP_GCLOUD_FREQUENCY=monthly mac-upkeep run  # Override frequency
```

### Sudoers

`mo_clean` and `mo_optimize` require passwordless sudo for the `mo` binary:

```bash
mac-upkeep setup | sudo tee /etc/sudoers.d/mac-upkeep && sudo chmod 0440 /etc/sudoers.d/mac-upkeep
sudo visudo -c
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and conventions.

## License

MIT
