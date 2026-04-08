# mac-upkeep

Automated macOS maintenance CLI. Runs weekly via `brew services` to keep your dev environment clean.

> **Requires macOS** — uses Homebrew, launchd, and macOS notifications.

## Install

```bash
brew install calvindotsg/tap/mac-upkeep
brew services start mac-upkeep  # Monday 12 PM weekly
```

Or via pip/uvx:

```bash
pip install mac-upkeep
uvx mac-upkeep run       # one-off without installing
```

## Tasks

Homebrew updates, dev tool cache pruning (gcloud, pnpm, uv), Fish plugin updates, system optimization via [mole](https://github.com/nicehash/mole), and Brewfile enforcement.

```bash
mac-upkeep tasks  # See all tasks with frequency and last-run status
```

Tasks auto-detect installed tools — missing tools are skipped. Each task runs on a weekly or monthly schedule. Use `--force <task>` to run a specific task on demand.

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

## License

MIT
