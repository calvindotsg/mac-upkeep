# mac-upkeep

[![PyPI](https://img.shields.io/pypi/v/mac-upkeep)](https://pypi.org/project/mac-upkeep/)
[![CI](https://img.shields.io/github/actions/workflow/status/calvindotsg/mac-upkeep/test.yml?branch=main)](https://github.com/calvindotsg/mac-upkeep/actions)
[![Python](https://img.shields.io/pypi/pyversions/mac-upkeep)](https://pypi.org/project/mac-upkeep/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![macOS](https://img.shields.io/badge/platform-macOS-lightgrey?logo=apple&logoColor=black)](https://github.com/calvindotsg/mac-upkeep)

Automated macOS maintenance CLI. Runs Homebrew updates, dev tool cache cleanup (gcloud, pnpm, uv), Fish plugin updates, system optimization, and Brewfile enforcement on boot + weekly via `brew services` — zero config required.

![mac-upkeep demo](https://raw.githubusercontent.com/calvindotsg/mac-upkeep/main/demo/demo.gif)

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
| `mo_clean` | Clean system and user caches ([Mole](https://github.com/tw93/Mole)) | Weekly |
| `mo_optimize` | Optimize DNS, Spotlight, fonts, Dock ([Mole](https://github.com/tw93/Mole)) | Weekly |
| `mo_purge` | Remove old project artifacts ([Mole](https://github.com/tw93/Mole)) | Monthly |
| `brew_cleanup` | Remove old versions and cache files | Monthly |
| `brew_bundle` | Remove packages not in Brewfile | Weekly |
| `git_sync` | Pull configured git repositories | Daily |

Tasks auto-detect installed tools — missing tools are skipped. Use `--force <task>` to run a specific task on demand.

```bash
mac-upkeep tasks  # See all tasks with status, frequency, and next run
```

## Usage

```bash
mac-upkeep run                       # Run tasks (frequency-checked)
mac-upkeep run --dry-run             # Preview without executing
mac-upkeep run --force brew_update   # Run only brew_update
mac-upkeep run --force all           # Run all, ignoring schedule
mac-upkeep run --debug               # Verbose output
mac-upkeep tasks                     # List tasks with status and next run
mac-upkeep init                      # Generate config (detects your tools)
mac-upkeep show-config --default     # Show all available task options
mac-upkeep show-config               # Show your config overrides
mac-upkeep setup                     # Print sudoers rules
mac-upkeep status                    # Show scheduling dashboard
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

# Change frequency (daily, weekly, or monthly)
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

### git_sync

Pull configured git repositories daily with `git pull --ff-only`. Opt-in — list your repos explicitly:

```toml
[git_sync]
repos = [
    "~/code/my-project",
    "~/work/max-*",       # glob patterns supported
]
skip_dirty = true         # skip repos with uncommitted changes
```

Each repo is skipped with a reason if it's not a git repo, has no remote, has no upstream branch, or (when `skip_dirty = true`) has uncommitted changes.

#### Authentication

Any of the following work under launchd without mac-upkeep-side configuration:

- **SSH + `IdentityAgent` (recommended under launchd):** a path-based entry in `~/.ssh/config` pointing at any SSH agent's UNIX socket. Works because the directive is a file path, not the `SSH_AUTH_SOCK` env var that launchd would strip.
- **HTTPS + credential helper:** `gh auth setup-git` or `git config --global credential.helper osxkeychain`. Requires the helper binary on the launchd `PATH`.
- **`[url].insteadOf` rewrite:** force SSH regardless of remote protocol by rewriting `https://<host>/` in `~/.gitconfig` to a matching SSH `Host` alias. Bypasses HTTPS auth entirely.

git_sync sets `GIT_TERMINAL_PROMPT=0` and a no-op `GIT_ASKPASS` default (user-set `GIT_ASKPASS` is respected) so misconfigured auth fails in milliseconds instead of stalling to the 60 s subprocess timeout.

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
