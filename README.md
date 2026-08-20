# mac-upkeep

[![PyPI](https://img.shields.io/pypi/v/mac-upkeep)](https://pypi.org/project/mac-upkeep/)
[![CI](https://img.shields.io/github/actions/workflow/status/calvindotsg/mac-upkeep/test.yml?branch=main)](https://github.com/calvindotsg/mac-upkeep/actions)
[![Python](https://img.shields.io/pypi/pyversions/mac-upkeep)](https://pypi.org/project/mac-upkeep/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/calvindotsg/mac-upkeep/blob/main/LICENSE)
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
| `mo_clean` | Clean user caches ([Mole](https://github.com/tw93/Mole)) | Weekly |
| `mo_optimize` | Optimize DNS, Spotlight, fonts, Dock ([Mole](https://github.com/tw93/Mole)) | Off by default |
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
mac-upkeep setup                     # Print log-rotation config
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

Task fields are type-checked. TOML booleans are unquoted — `enabled = false`, not
`enabled = "false"` — and a quoted one is now rejected with an error rather than
silently leaving the task enabled. Task names are matched case- and space-insensitively
(`[tasks.Docker Prune]` and `[tasks.docker_prune]` are the same task, and declaring both
is an error).

#### Brewfile discovery

With no `[paths] brewfile` and no `MAC_UPKEEP_BREWFILE`/`HOMEBREW_BUNDLE_FILE`, only two
absolute locations are searched: `$XDG_CONFIG_HOME/Brewfile` (default `~/.config/Brewfile`)
and `~/.Brewfile`. **The current working directory is deliberately not searched.**
`brew bundle` evaluates a Brewfile as Ruby and `brew_bundle` runs `cleanup --force`, so a
CWD-relative lookup made whichever project directory your shell happened to be in able to
run code and uninstall every package it did not list. If no Brewfile is found the task
skips; it no longer runs with an empty `--file=`, which Homebrew resolved back to
`$PWD/Brewfile`.

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

#### Only enrol repositories you created

**A git repository's own `.git/config` can make git execute commands.** Point a `repos` glob only at directories you created yourself — never at a downloads, sync, backup, or vendor-drop directory. A tree that arrives by archive, restore, or file sync brings its `.git/config` and `.git/hooks` with it, and `safe.directory` does not help: it keys on ownership, and anything you unpacked is owned by you. A plain `git clone` does *not* carry these, so cloning is unaffected.

mac-upkeep neutralises the directives it can, on every git call — not just on `pull`, because `git status --porcelain` (the `skip_dirty` check itself) is enough to trigger some of them:

| Neutralised | How |
|---|---|
| `core.fsmonitor` | reset to empty |
| `.git/hooks/*` | `core.hooksPath=/dev/null` |
| `credential.helper` | list reset, then your own global/system helpers re-added |
| `core.sshCommand` | set to your own global/system value, else explicitly `ssh` |
| `ext::` transport | `protocol.ext.allow=never` |
| `remote.<name>.uploadpack` | `protocol.file.allow=never` |
| `core.gitProxy` | `protocol.git.allow=never` |

Note the asymmetry in the middle two rows. `credential.helper` is multi-valued, so an empty entry resets git's accumulated list. `core.sshCommand` is single-valued, so an empty entry is not a reset — git would try to execute the empty string and every SSH remote would fail. Single-valued keys are therefore *set*, never blanked.

Three consequences worth knowing:

- **Local-path remotes no longer work** under git_sync (`fatal: transport 'file' not allowed`). This is deliberate — it is what closes the `uploadpack` execution path. Pull from a bare mirror on an external disk outside mac-upkeep.
- **`git://` remotes no longer work** (`fatal: transport 'git' not allowed`). Also deliberate: `core.gitProxy` runs an arbitrary command for that transport and cannot be neutralised any other way. `git://` is unauthenticated and unencrypted; use SSH or HTTPS.
- **Repository hooks do not run** during git_sync, so a `post-merge` hook that installs dependencies will not fire on an unattended pull.

This is defence in depth, not a sandbox. Git has no "ignore this repository's config" switch. The known remaining execution path is `filter.<driver>.clean` from a planted `.gitattributes`, which fires on `git status`: driver names are arbitrary, so no fixed override covers them. Enrolment discipline is the control that actually holds.

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

`MAC_UPKEEP_<TASK>` enables the task only for an explicitly truthy value —
`true`, `1`, `yes`, or `on`. **Anything else disables it**, including `off`, `disabled`
and the empty string, which previously all *enabled* the task by falling through a
denylist of `false`/`0`/`no`.

### Why there is no sudoers file

**mac-upkeep runs every task as you.** Nothing it ships needs root.

Releases before 4.0.0 ran `mo clean` and `mo optimize` under `sudo -n` and told you to
install a NOPASSWD rule naming `$(brew --prefix)/bin/mo`. That path is a bash script inside
a **user-writable** Homebrew prefix which transitively sources around thirty more user-owned
`.sh` files — so any code already running as you could rewrite what root would execute, and
then just wait for the weekly LaunchAgent run. A `sha256` `Digest_Spec` does not fix it: the
digest covers the one entry script, and `sudoers(5)` documents digests as TOCTOU-racy when
the command's directory is user-writable.

> **⚠ Upgrading from < 4.0.0 — action required**
>
> The sudoers file was installed manually, so `brew upgrade` does **not** remove it. Delete it:
>
> ```bash
> sudo rm -f /etc/sudoers.d/mac-upkeep
> sudo visudo -c                       # must print "parsed OK"
> ```

**What changes in practice.** `mo clean` still runs weekly and still clears your user caches;
non-interactively it probes for an existing sudo ticket with `sudo -n -v`, which never
prompts, and simply skips the system-level half when there is none. The measured cost of
losing that half is about 7 MB per week of files under `/private/var/log`.

`mo optimize` is now **off by default** rather than merely unprivileged. Its work — DNS
flush, font database reset, route/ARP flush, Spotlight reindex, disk permissions — is
root-only, and `bin/optimize.sh` asks for admin access unconditionally with no
non-interactive guard, so under launchd it would raise a macOS password dialog that sits on
screen until the task times out. Run it by hand when you want it:

```bash
mo optimize
```

Re-enable it for interactive use if you prefer, with `[tasks.mo_optimize]` / `enabled = true`
in your config — but understand it will prompt.

### Log file permissions

`mac-upkeep setup` now prints the `newsyslog.d` line with mode **640** instead of 644. The
log records `git_sync` failures by repository name, which enumerates your private and
employer-internal repositories, and `$(brew --prefix)/var/log` is world-traversable — unlike
`~/Library`, nothing else gates it. Owner plus the `admin` group keeps `mac-upkeep logs`
working.

> **⚠ Already installed?** `/etc/newsyslog.d/mac-upkeep.conf` is installed manually, so it is
> **not** upgraded by `brew upgrade` either, and existing log files keep their 644 mode. Rewrite
> the conf from `mac-upkeep setup`, then:
>
> ```bash
> sudo chmod 640 "$(brew --prefix)"/var/log/mac-upkeep.log*
> ```

## Contributing

See [CONTRIBUTING.md](https://github.com/calvindotsg/mac-upkeep/blob/main/CONTRIBUTING.md) for development setup and conventions.

## License

MIT
