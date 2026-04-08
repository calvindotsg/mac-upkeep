# maintenance

Automated macOS maintenance CLI. Runs weekly via `brew services` to keep your dev environment clean.

## Install

```bash
brew install calvindotsg/tap/maintenance
brew services start maintenance  # Monday 12 PM weekly
```

Or via pip/uvx:

```bash
pip install maintenance   # from PyPI (when published)
uvx maintenance run       # one-off without installing
```

## Tasks

Homebrew updates, dev tool cache pruning (gcloud, pnpm, uv), Fish plugin updates, system optimization via [mole](https://github.com/nicehash/mole), and Brewfile enforcement.

```bash
maintenance tasks  # See all tasks with frequency and last-run status
```

Tasks auto-detect installed tools — missing tools are skipped. Each task runs on a weekly or monthly schedule. Use `--force <task>` to run a specific task on demand.

## Usage

```bash
maintenance run                       # Run tasks (frequency-checked)
maintenance run --dry-run             # Preview without executing
maintenance run --force brew_update   # Run only brew_update
maintenance run --force all           # Run all, ignoring schedule
maintenance run --debug               # Verbose output
maintenance tasks                     # List tasks with status
maintenance init                      # Generate config (detects your tools)
maintenance show-config --default     # Show all available task options
maintenance show-config               # Show your config overrides
maintenance setup                     # Print sudoers rules
maintenance status                    # Show brew service status
maintenance logs                      # View last 20 log lines
maintenance logs -f                   # Follow logs
maintenance --version                 # Show version
```

## Configuration

Works out of the box with zero configuration. To customize, generate a starter config:

```bash
maintenance init
```

This probes your system, detects installed tools, and writes a commented config to `~/.config/maintenance/config.toml`. Only detected tasks are listed. Built-in defaults apply automatically — uncomment lines to override.

To see all available tasks and options:

```bash
maintenance show-config --default
```

### Override examples

```toml
# ~/.config/maintenance/config.toml

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
MAINTENANCE_GCLOUD=false maintenance run              # Disable a task
MAINTENANCE_GCLOUD_FREQUENCY=monthly maintenance run  # Override frequency
```

### Sudoers

`mo_clean` and `mo_optimize` require passwordless sudo for the `mo` binary:

```bash
maintenance setup | sudo tee /etc/sudoers.d/maintenance && sudo chmod 0440 /etc/sudoers.d/maintenance
sudo visudo -c
```

## License

MIT
