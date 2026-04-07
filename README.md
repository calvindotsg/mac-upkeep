# maintenance

Automated macOS maintenance CLI. Runs weekly via `brew services` to keep your dev environment clean.

## Install

```bash
brew install calvindotsg/tap/maintenance
brew services start maintenance  # Monday 12 PM weekly
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
maintenance setup                     # Print sudoers rules
maintenance status                    # Show brew service status
maintenance logs                      # View last 20 log lines
maintenance logs -f                   # Follow logs
maintenance --version                 # Show version
```

## Configuration

Copy the example config:

```bash
mkdir -p ~/.config/maintenance
cp "$(brew --prefix)/share/maintenance/config.example.toml" ~/.config/maintenance/config.toml
```

All tasks default to enabled. Disable via config or environment variable:

```toml
[tasks]
gcloud = false       # Skip gcloud updates
mo_optimize = false  # Skip system optimization

[frequency]
gcloud = "monthly"   # Override schedule (weekly or monthly)

[paths]
brewfile = "~/.config/Brewfile"
```

```bash
MAINTENANCE_GCLOUD=false maintenance run  # Env vars override config
```

`mo_clean` and `mo_optimize` require passwordless sudo for the `mo` binary:

```bash
maintenance setup | sudo tee /etc/sudoers.d/maintenance && sudo chmod 0440 /etc/sudoers.d/maintenance
sudo visudo -c
```

## License

MIT
